"""
VMI Update Process - Main Entry Point

Usage:
    python main.py                  # Price sync (default)
    python main.py -a orders        # Auto order submission
    python main.py -a orders -q     # Submit as quotes
    python main.py -l debug         # Enable debug logging
"""

from argparse import ArgumentParser
from sys import exit
import traceback
from log import configure_logs, log_debug, log_info, log_error, start_log, stop_log, log_shutdown, set_level
from utils import coalesce, email, rename_log, check_order, get_contract, classify_dropped_item
from db import connect_db, close_db_conn, get_items, update_item, get_orders, get_order_items, update_order, mark_inflight, clear_inflight, get_stale_inflight
from api import get_item, get_customer_name, create_order, approve_order, check_item_availability
from xml_processor import build_order, add_line_item, print_xml
import health


def items():
    # Sync item prices from P21 API to local Matrix database
    l_tot_cnt = 0
    l_succ_cnt = 0

    start_log('Update process')

    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()
        l_rows = get_items(l_cursor)

        for row in l_rows:
            l_tot_cnt += 1
            log_debug('**** API call for item: ' + row.item_code)
            l_item = get_item(row.item_code)

            if 'ResourceError' in l_item.keys():
                log_error('Item not found in API: ' + row.item_code)
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

        health.record_run('items', 'success', l_succ_cnt, l_tot_cnt)

        email('Matrix Auto Price Changes for ' + get_customer_name())
        rename_log()
    except Exception:
        health.record_event('run_failure', traceback.format_exc()[:2000])
        health.record_run('items', 'error', l_succ_cnt, l_tot_cnt)
        raise


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

        l_orders = get_orders(l_cursor)

        if len(l_orders) == 0:
            close_db_conn(l_db_conn)
            log_debug('')
            log_debug('No new orders found')
            log_debug('')
            log_debug('*********** Create New Orders process completed ***********')
            health.record_run('orders', 'success', 0, 0)
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

                elif l_status == 'partial':
                    l_order_no = l_response
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
                    clear_inflight(l_cursor, l_order.po_key)
                    l_succ_cnt += 1

                else:
                    l_order_no = l_response
                    update_order(l_cursor, l_order.po_key)
                    log_info('Order created: P21 OrderNo = ' + l_order_no + ' and po_code = ' + str(l_order.po_code))
                    clear_inflight(l_cursor, l_order.po_key)
                    l_succ_cnt += 1

            except Exception as e:
                log_error('Submitting order to API failed:\n' + str(e))
                # erp_send_state intentionally stays 'inflight' here: the
                # outcome of create_order() is unknown (it may have reached
                # P21 before this failed), so leave the guard in place rather
                # than risk a duplicate submission next run. get_stale_inflight()
                # flags it after an hour for manual review.

        close_db_conn(l_db_conn)
        stop_log('Create New Orders process', l_succ_cnt, l_tot_cnt)

        health.record_run('orders', 'success', l_succ_cnt, l_tot_cnt)

        email('Matrix Auto Order Submission for ' + get_customer_name())
        rename_log()
    except Exception:
        health.record_event('run_failure', traceback.format_exc()[:2000])
        health.record_run('orders', 'error', l_succ_cnt, l_tot_cnt)
        raise


def main():
    configure_logs()
    parser = ArgumentParser(description='VMI Update Process')
    actions = ['items', 'orders']
    levels = ['debug', 'info', 'warn', 'error']
    parser.add_argument('-a', choices=actions, default='items', dest='action')
    parser.add_argument('-l', choices=levels, default='info', dest='level')
    parser.add_argument('-q', '--quote', action='store_true')
    args = parser.parse_args()

    set_level(args.level.upper())

    if args.action.lower() == 'orders':
        orders(args.quote)
    elif args.action.lower() == 'items':
        items()


if __name__ == "__main__":
    main()
