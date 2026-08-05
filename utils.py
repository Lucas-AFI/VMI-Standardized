"""
VMI Update Process - Utility Functions
Handles order validation, email notifications, and helper functions
"""

from smtplib import SMTP, SMTPException, SMTPServerDisconnected, SMTPConnectError
from ssl import create_default_context
from os import path, rename
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from log import l_log_location, log_debug, log_error
from datetime import datetime
from time import sleep
from config import get_email_to, get_email_cc, get_contract_id
import credentials
import health

SMTP_FROM = 'afireports@afi-tools.com'

SMTP_TIMEOUT = 15          # seconds, per attempt
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2  # doubles each attempt: 2s, 4s, 8s


def check_order(p_dict, p_item_list):
    """
    Validate P21 order response and determine status.
    Returns: (status, order_no_or_reason, message, dropped_item_ids)
      - 'success' : all items accepted
      - 'partial' : order created but some items were dropped (Delete=Y)
      - 'error'   : order was not created
    dropped_item_ids is always a list; only non-empty when status == 'partial'.
    """
    if 'ResourceError' in p_dict:
        l_error_message = str(p_dict['ResourceError'].get('ErrorMessage', 'Unknown Error'))
        if 'This item ID is not valid' in l_error_message:
            l_err_message = 'One or more item IDs are not valid: ' + ','.join(p_item_list)
            return 'error', l_err_message, l_err_message, []
        else:
            l_full_error = 'Unknown Error: ' + l_error_message + ' Items: ' + ','.join(p_item_list)
            return 'error', l_full_error, l_full_error, []

    l_items = p_dict['Order']['Lines']['OrderLine']
    l_message = ""
    l_dropped_ids = []

    if type(l_items) is dict:
        if l_items['Delete'] == 'Y':
            l_message = 'OrderNo: ' + p_dict['Order']['OrderNo'] + '\nItemId: ' + l_items['ItemId'] + ' is not available to purchase\n'
            l_dropped_ids = [l_items['ItemId']]
    else:
        if any(d['Delete'] == 'Y' for d in l_items):
            l_message = 'OrderNo: ' + p_dict['Order']['OrderNo'] + '\n'
            for item in l_items:
                if item['Delete'] == 'Y':
                    l_message += 'ItemId: ' + item['ItemId'] + ' is not available to purchase\n'
                    l_dropped_ids.append(item['ItemId'])

    if l_message == "":
        return 'success', p_dict['Order']['OrderNo'], l_message, l_dropped_ids
    else:
        return 'partial', p_dict['Order']['OrderNo'], l_message, l_dropped_ids


def classify_dropped_item(p_availability):
    """
    Classify the probable cause of a dropped order item, given P21's
    itemsAvailability response for it (see api.check_item_availability()).

    p_availability: the dict for one ItemId from check_item_availability()'s
    result, or None if that item was missing from the response (including
    if the availability call itself failed - check_item_availability()
    returns {} in that case, so every lookup misses and every item is
    tagged 'unknown' rather than guessing).

    QuantityAvailable is used, not QuantityOnHand - it already nets out
    what's allocated/frozen/quarantined, so it reflects what's actually
    sellable right now. Stock could physically exist but be fully spoken for.
    """
    if not p_availability:
        return 'unknown'
    try:
        l_qty_available = float(p_availability.get('QuantityAvailable', 0) or 0)
    except (TypeError, ValueError):
        return 'unknown'
    return 'stock_out' if l_qty_available <= 0 else 'possible_mapping_issue'


def coalesce(p_value):
    """Handle None type/null for existing pricing in SQL Server"""
    if p_value is None:
        return 'null'
    return '{:.4f}'.format(p_value)


def get_contract():
    """Get P21 contract ID from config, default to NONE"""
    return get_contract_id() or 'NONE'


def rename_log():
    """Rename log file with timestamp to preserve history"""
    l_timestamp = datetime.now().strftime('%Y_%m_%d_%H%M%S')
    rename(l_log_location, l_log_location[:-4] + '_' + l_timestamp + '.log')


def _is_retryable_smtp_error(p_exc):
    """
    True for connection-level failures (network/SSL/timeout) worth retrying.
    False for protocol-level rejections (bad auth, refused recipients, bad
    data, etc.) that will fail identically on retry.

    Note: smtplib.SMTPException itself is a subclass of OSError in Python 3
    -- a bare `isinstance(e, OSError)` check would therefore also match
    things like SMTPAuthenticationError and incorrectly retry them. This
    explicitly excludes any SMTPException that isn't one of the two
    "the connection itself didn't work" subtypes.
    """
    if isinstance(p_exc, (SMTPServerDisconnected, SMTPConnectError)):
        return True
    return isinstance(p_exc, OSError) and not isinstance(p_exc, SMTPException)


def _send_email_with_retry(p_msg, p_all_recip):
    """
    Do the actual SMTP send, retrying transient connection failures with
    backoff -- same philosophy as api.py's _request_with_retry(). See
    _is_retryable_smtp_error() for exactly what counts as "transient".

    Returns sendmail()'s result: a dict of any individually-refused
    recipients, even when the overall call "succeeds" (previously silently
    discarded). Raises on total failure -- email() below is responsible for
    catching this and never letting it propagate further.
    """
    l_last_exc = None
    for l_attempt in range(RETRY_ATTEMPTS):
        try:
            context = create_default_context()
            s = SMTP('smtp.sendgrid.net', 587, timeout=SMTP_TIMEOUT)
            s.starttls(context=context)
            s.login('apikey', credentials.get_sendgrid_api_key())
            l_refused = s.sendmail(SMTP_FROM, p_all_recip, p_msg.as_string())
            s.quit()
            return l_refused
        except Exception as e:
            if not _is_retryable_smtp_error(e):
                raise
            l_last_exc = e
            log_debug('Email send attempt ' + str(l_attempt + 1) + '/' + str(RETRY_ATTEMPTS) + ' failed (connection issue): ' + str(e))
            if l_attempt < RETRY_ATTEMPTS - 1:
                sleep(RETRY_BACKOFF_SECONDS * (2 ** l_attempt))
    raise l_last_exc


def email(p_subject, p_message="", p_log=True, p_attach=True):
    """
    Send email notification with optional log file attachment.

    Args:
        p_subject : email subject line
        p_message : email body (used when p_log=False)
        p_log     : if True, attach/embed the log file
        p_attach  : if True, attach log as file; if False, embed in body

    Never raises. Retries transient connection failures; on total failure
    (retries exhausted, or a non-retryable rejection like bad auth or a
    refused recipient) logs it and records an 'email_failure' health event,
    then returns normally either way. A notification failure must never be
    mistaken for the actual price sync / order submission work having
    failed, and must never block that work's own state tracking
    (clear_inflight/record_open_order/rename_log/etc.) from completing --
    this mirrors the SSL failure that killed American Torch Tip's price
    sync (see api.py's _request_with_retry), and fixes a latent bug where a
    mid-loop email hiccup in orders() could previously leave a
    successfully-submitted order's erp_send_state stuck 'inflight' just
    because the *notification* about it failed.
    """
    l_to = get_email_to()
    l_cc = get_email_cc()
    l_all_recip = l_to + l_cc

    msg = MIMEMultipart()
    msg['Subject'] = p_subject
    msg['From'] = SMTP_FROM
    msg['To'] = ','.join(l_to)
    if l_cc:
        msg['Cc'] = ','.join(l_cc)

    if p_log:
        with open(l_log_location, 'r') as f:
            mess = f.read()
        if p_attach:
            part = MIMEApplication(mess, Name=path.basename(l_log_location))
            part['Content-Disposition'] = 'attachment; filename="%s"' % path.basename(l_log_location)
        else:
            part = MIMEText(mess)
        msg.attach(part)
    else:
        msg.attach(MIMEText(p_message or ''))

    try:
        l_refused = _send_email_with_retry(msg, l_all_recip)
    except Exception as e:
        log_error('Email send failed for "' + p_subject + '": ' + str(e))
        health.record_event('email_failure', 'Subject: ' + p_subject + ' -- ' + str(e))
        return

    if l_refused:
        log_error('Email "' + p_subject + '" partially failed -- recipient(s) refused: ' + str(l_refused))
        health.record_event('email_failure', 'Subject: ' + p_subject + ' -- recipients refused: ' + str(l_refused))
    else:
        log_debug('Email sent: ' + p_subject)