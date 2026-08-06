"""
VMI Update Process - Main Entry Point

Usage:
    python main.py                  # Price sync (default)
    python main.py -a orders        # Auto order submission
    python main.py -a orders -q     # Submit as quotes
    python main.py -a images        # Item image sync
    python main.py -l debug         # Enable debug logging
"""

from argparse import ArgumentParser
from sys import exit
from time import sleep
import traceback
from log import configure_logs, log_debug, log_info, log_error, start_log, stop_log, log_shutdown, set_level
from utils import coalesce, email, rename_log, check_order, get_contract, classify_dropped_item
from db import connect_db, close_db_conn, get_items, update_item, get_orders, get_order_items, update_order, mark_inflight, clear_inflight, get_stale_inflight, record_open_order, get_open_orders, clear_open_order
from api import get_item, get_customer_name, create_order, approve_order, check_item_availability, get_order_status
from xml_processor import build_order, add_line_item, print_xml
from images import sync_images
import health

# Threshold for flagging a P21 order that's sat open (not yet Completed/
# Cancelled/Deleted) too long without being received/closed -- see
# check_open_orders() below. Expected to be tuned later.
STALE_OPEN_ORDER_DAYS = 3

# How many times (and how long to wait between tries) to ask P21 to confirm
# a just-created order actually exists there, before giving up -- see
# confirm_order_created() below. A single immediate GET right after the
# create_order() POST can race P21's own indexing of the new order, so this
# gives it a few seconds' grace rather than treating a false-negative as a
# real failure.
ORDER_CONFIRM_ATTEMPTS = 3
ORDER_CONFIRM_RETRY_DELAY = 5

# Price sync only -- if this many get_item() calls in a row fail on a
# genuine connection problem (not a routine "item not found"), the
# connection itself is probably down rather than just having a bad moment.
# Pause to give it a real chance to recover instead of burning through the
# rest of the catalog one already-doomed call at a time. Capped per run so a
# connection that never recovers still finishes in bounded time instead of
# pausing indefinitely.
PRICE_CONSECUTIVE_FAILURE_THRESHOLD = 5
PRICE_PAUSE_SECONDS = 180
PRICE_MAX_PAUSES_PER_RUN = 3


def items():
    # Sync item prices from P21 API to local Matrix database
    l_tot_cnt = 0
    l_succ_cnt = 0
    l_err_cnt = 0
    l_consecutive_failures = 0
    l_pauses_used = 0

    start_log('Update process')

    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()
        l_rows = get_items(l_cursor)

        for row in l_rows:
            l_tot_cnt += 1
            log_debug('**** API call for item: ' + row.item_code)

            try:
                l_item = get_item(row.item_code)
            except Exception as e:
                # A connection failure here (all of get_item()'s own escalating
                # retries already exhausted) must never take down the rest of
                # the catalog -- leave this item's price exactly as it already
                # is (no update_item() call happens) and let the next run pick
                # it back up. l_err_cnt already covers "we couldn't confirm
                # this item's price this run" regardless of the reason.
                log_error('Price lookup failed for item ' + row.item_code + ' (connection issue, will retry next run): ' + str(e))
                l_err_cnt += 1
                l_consecutive_failures += 1

                if l_consecutive_failures >= PRICE_CONSECUTIVE_FAILURE_THRESHOLD and l_pauses_used < PRICE_MAX_PAUSES_PER_RUN:
                    l_pauses_used += 1
                    log_error(
                        str(l_consecutive_failures) + ' consecutive price lookups failed -- pausing ' +
                        str(PRICE_PAUSE_SECONDS) + 's to let the connection recover (pause ' +
                        str(l_pauses_used) + '/' + str(PRICE_MAX_PAUSES_PER_RUN) + ' for this run)'
                    )
                    health.record_event(
                        'connection_degraded',
                        str(l_consecutive_failures) + ' consecutive price lookup failures -- paused ' +
                        str(PRICE_PAUSE_SECONDS) + 's (pause ' + str(l_pauses_used) + '/' + str(PRICE_MAX_PAUSES_PER_RUN) + ' this run)'
                    )
                    sleep(PRICE_PAUSE_SECONDS)
                    l_consecutive_failures = 0

                continue

            l_consecutive_failures = 0

            if 'ResourceError' in l_item.keys():
                log_error('Item not found in API: ' + row.item_code)
                l_err_cnt += 1
            else:
                l_new_price = '{:.4f}'.format(float(l_item['ItemPrice']['UnitPrice']))
                l_old_price = coalesce(row.item_price)

                if l_new_price != l_old_price:
                    if float(l_new_price) != 0:
                        try:
                            update_item(l_cursor, row.item_key, row.item_code, l_new_price, l_old_price)
                            l_succ_cnt += 1
                        except Exception as e:
                            log_error(e)
                else:
                    log_debug('No update: price is the same for item: ' + str(row.item_code))

        close_db_conn(l_db_conn)
        stop_log('Update process', l_succ_cnt, l_tot_cnt)

        health.record_run('items', 'success', l_succ_cnt, l_tot_cnt, l_err_cnt)

        email('Matrix Auto Price Changes for ' + get_customer_name())
        rename_log()
    except Exception:
        # Full traceback, not truncated -- goes to app.log (the only durable
        # local copy; the bare `raise` below propagates the original
        # exception uncaught, which prints to stderr, not through logging,
        # so without this log_error() call the full detail existed nowhere
        # persistent) and to the dashboard event untruncated too, since
        # NVARCHAR(MAX)/SQLite TEXT have no practical size limit that would
        # justify cutting it off.
        l_traceback = traceback.format_exc()
        log_error('Unhandled exception in items():\n' + l_traceback)
        health.record_event('run_failure', l_traceback)
        health.record_run('items', 'error', l_succ_cnt, l_tot_cnt, l_err_cnt)
        raise


def _order_is_resolved(p_order_status):
    # P21's API reference only documents these as untyped strings (no
    # enumerated Y/N values confirmed) -- treat any common truthy spelling as
    # resolved so this errs toward under- rather than over-flagging. Verify
    # against a real response before relying on this, and adjust if P21
    # turns out to use a different convention.
    l_order = (p_order_status or {}).get('Order') or {}
    l_flags = (l_order.get('Completed'), l_order.get('CancelledFlag'), l_order.get('DeletedFlag'))
    return any(str(f).strip().upper() in ('Y', 'YES', 'TRUE', '1') for f in l_flags if f)


def check_open_orders(l_cursor):
    # For every P21 order still being tracked as open, ask P21 for its
    # current status: stop tracking it once resolved (Completed/Cancelled/
    # Deleted), or flag it once -- event_already_recorded() keeps this to one
    # row per PO, same pattern as stale_inflight_order -- once it's been open
    # longer than STALE_OPEN_ORDER_DAYS. See erp_open_orders.sql.
    for l_open_order in get_open_orders(l_cursor):
        try:
            l_status = get_order_status(l_open_order.order_no)
        except Exception as e:
            log_error('Open-order status check failed for OrderNo ' + str(l_open_order.order_no) + ': ' + str(e))
            continue

        if 'ResourceError' in l_status:
            log_error(
                'Open-order status check returned ResourceError for OrderNo ' +
                str(l_open_order.order_no) + ': ' + str(l_status['ResourceError'])
            )
            continue

        if _order_is_resolved(l_status):
            clear_open_order(l_cursor, l_open_order.po_key)
            continue

        if l_open_order.days_open >= STALE_OPEN_ORDER_DAYS:
            l_po_code = str(l_open_order.po_code or l_open_order.order_no)
            if not health.event_already_recorded('stale_open_order', l_po_code):
                health.record_event(
                    'stale_open_order',
                    'Order ' + str(l_open_order.order_no) + ' (po_code = ' + str(l_open_order.po_code or '') +
                    ') has been open ' + str(l_open_order.days_open) + ' day(s) without being received/closed',
                    l_po_code
                )


def _order_confirmed(p_order_no, p_order_status):
    if not p_order_status or 'ResourceError' in p_order_status:
        return False
    l_order = p_order_status.get('Order') or {}
    return str(l_order.get('OrderNo') or '') == str(p_order_no)


def confirm_order_created(p_order_no):
    # check_order() only ever validates create_order()'s own POST response --
    # it never independently checks that P21 actually persisted the order.
    # This closes that gap with a follow-up GET (see get_order_status() in
    # api.py), retrying briefly in case of an indexing race, before treating
    # it as a real "P21 doesn't have this" failure rather than a POST
    # response that merely looked fine.
    for l_attempt in range(ORDER_CONFIRM_ATTEMPTS):
        try:
            l_status = get_order_status(p_order_no)
        except Exception as e:
            log_error('Order confirmation check failed for OrderNo ' + str(p_order_no) + ': ' + str(e))
            l_status = None

        if _order_confirmed(p_order_no, l_status):
            return True

        if l_attempt < ORDER_CONFIRM_ATTEMPTS - 1:
            sleep(ORDER_CONFIRM_RETRY_DELAY)

    return False


def orders(p_quote=None):
    # Submit pending Matrix purchase orders to P21
    l_tot_cnt = 0
    l_succ_cnt = 0

    start_log('Create New Orders process')

    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()

        l_stale = get_stale_inflight(l_cursor)
        if l_stale:
            l_stale_pos = ', '.join(str(r.po_key) for r in l_stale)
            log_error('Orders stuck in-flight for over 1 hour (possible crash mid-submission): ' + l_stale_pos)
            email(
                'Matrix Auto Order ALERT - Stale In-Flight Orders for ' + get_customer_name(),
                'The following po_key(s) have been marked in-flight for over 1 hour and were NOT '
                'automatically resubmitted:\n' + l_stale_pos +
                '\n\nManually verify in P21 whether these orders were created, then either delete '
                'the erp_send_state row (if not actually sent) or set send_erp = 1 (if sent) as appropriate.',
                False
            )
            for l_stale_row in l_stale:
                if not health.event_already_recorded('stale_inflight_order', str(l_stale_row.po_key)):
                    health.record_event(
                        'stale_inflight_order',
                        'Orders stuck in-flight for over 1 hour (possible crash mid-submission): ' + str(l_stale_row.po_key),
                        str(l_stale_row.po_key)
                    )

        check_open_orders(l_cursor)

        l_orders = get_orders(l_cursor)

        if len(l_orders) == 0:
            close_db_conn(l_db_conn)
            log_debug('')
            log_debug('No new orders found')
            health.record_run('orders', 'success', 0, 0)
            stop_log('Create New Orders process', 0, 0)
            rename_log()
            exit()

        for l_order in l_orders:
            l_tot_cnt += 1
            l_xml = build_order(l_order, p_quote)

            try:
                l_order_items = get_order_items(l_cursor, l_order.po_key)
                l_xml, l_item_ids = add_line_item(l_xml, l_order_items)
            except Exception as e:
                log_error('Building XML document failed:\n' + str(e))
                continue

            mark_inflight(l_cursor, l_order.po_key)

            try:
                l_order_resp = create_order(l_xml)
                l_status, l_response, l_message, l_dropped_item_ids = check_order(l_order_resp, l_item_ids)

                if l_status == 'error':
                    log_error('Submitting order to API failed: order not created. Reason: ' + l_response + '. po_code = ' + str(l_order.po_code or ''))
                    email('Matrix Auto Order Error for ' + get_customer_name(), 'Order not created because: ' + l_response + '\npo_code = ' + str(l_order.po_code or ''), False)
                    health.record_event('order_error', l_message, str(l_order.po_code or ''))
                    clear_inflight(l_cursor, l_order.po_key)

                else:
                    l_order_no = l_response

                    if not confirm_order_created(l_order_no):
                        log_error(
                            'Order ' + l_order_no + ' (po_code = ' + str(l_order.po_code or '') +
                            ') was reported created by P21 but could not be confirmed after retrying -- '
                            'leaving in-flight for manual review'
                        )
                        email(
                            'Matrix Auto Order ALERT - Unconfirmed Order for ' + get_customer_name(),
                            'P21 reported order ' + l_order_no + ' (po_code = ' + str(l_order.po_code or '') +
                            ') as created, but a follow-up check could not confirm it actually exists in P21.'
                            '\n\nManually verify in P21 whether this order exists, then either delete the '
                            'erp_send_state row (if not actually sent) or set send_erp = 1 (if it did go '
                            'through) as appropriate.',
                            False
                        )
                        health.record_event(
                            'order_not_confirmed',
                            'OrderNo ' + l_order_no + ' was reported created but P21 did not confirm it exists after retrying',
                            str(l_order.po_code or '')
                        )
                        continue

                    if l_status == 'partial':
                        update_order(l_cursor, l_order.po_key)
                        log_info('Order created with skipped items: P21 OrderNo = ' + l_order_no + ' and po_code = ' + str(l_order.po_code))

                        # Diagnostic-only: check why the dropped item(s) were unavailable
                        # (stock-out vs. a likely SKU mapping issue), batched into one
                        # P21 call per order. Purely additive -- never blocks or crashes
                        # the partial-order handling above, which already worked before this.
                        l_availability = check_item_availability(l_dropped_item_ids)
                        l_cause_lines = [
                            'ItemId: ' + l_dropped_id + ' - Probable cause: ' + classify_dropped_item(l_availability.get(l_dropped_id))
                            for l_dropped_id in l_dropped_item_ids
                        ]
                        l_message_with_cause = l_message + ('\n' + '\n'.join(l_cause_lines) + '\n' if l_cause_lines else '')

                        log_error('Items skipped in order ' + l_order_no + ':\n' + l_message_with_cause)
                        email('Matrix Auto Order Item Exception(s) for ' + get_customer_name(), l_message_with_cause, False)
                        health.record_event('partial_order', l_message_with_cause, str(l_order.po_code or ''))
                    else:
                        update_order(l_cursor, l_order.po_key)
                        log_info('Order created: P21 OrderNo = ' + l_order_no + ' and po_code = ' + str(l_order.po_code))

                    clear_inflight(l_cursor, l_order.po_key)
                    if not p_quote:
                        record_open_order(l_cursor, l_order.po_key, str(l_order.po_code or ''), l_order_no)
                    l_succ_cnt += 1

            except Exception as e:
                log_error('Submitting order to API failed:\n' + str(e))
                # erp_send_state intentionally stays 'inflight' here: the
                # outcome of create_order() is unknown (it may have reached
                # P21 before this failed), so leave the guard in place rather
                # than risk a duplicate submission next run. get_stale_inflight()
                # flags it after an hour for manual review -- but that's up to
                # an hour of silence on the dashboard before anyone would know.
                # Record it immediately too, so a human can go verify against
                # P21 right away rather than waiting for the stale sweep.
                health.record_event(
                    'order_submission_error',
                    'Submitting order to P21 failed with an unknown outcome (may or may not have reached P21): ' + str(e),
                    str(l_order.po_code or '')
                )

        close_db_conn(l_db_conn)
        stop_log('Create New Orders process', l_succ_cnt, l_tot_cnt)

        health.record_run('orders', 'success', l_succ_cnt, l_tot_cnt)

        email('Matrix Auto Order Submission for ' + get_customer_name())
        rename_log()
    except Exception:
        l_traceback = traceback.format_exc()
        log_error('Unhandled exception in orders():\n' + l_traceback)
        health.record_event('run_failure', l_traceback)
        health.record_run('orders', 'error', l_succ_cnt, l_tot_cnt)
        raise


def main():
    configure_logs()
    parser = ArgumentParser(description='VMI Update Process')
    actions = ['items', 'orders', 'images']
    levels = ['debug', 'info', 'warn', 'error']
    parser.add_argument('-a', choices=actions, default='items', dest='action')
    parser.add_argument('-l', choices=levels, default='info', dest='level')
    parser.add_argument('-q', '--quote', action='store_true')
    args = parser.parse_args()

    set_level(args.level.upper())

    if args.action.lower() == 'orders':
        orders(args.quote)
    elif args.action.lower() == 'images':
        sync_images()
    elif args.action.lower() == 'items':
        items()


if __name__ == "__main__":
    main()
