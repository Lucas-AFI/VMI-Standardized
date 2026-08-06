"""
VMI Update Process - AFI P21 API Integration

API credentials are stored in Windows Credential Manager via collect_config.py.
Machine-specific settings are read from config.ini via config.py.
"""

import requests
from requests.exceptions import RequestException
from time import sleep
import xmltodict
from xml_processor import tostring
from credentials import get_base_url, get_api_username, get_api_password
from config import get_customer_id, get_location_id
from log import log_debug, log_error

l_base_url = get_base_url()

REQUEST_TIMEOUT = 15          # seconds, per attempt
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2      # doubles each attempt: 2s, 4s, 8s

# Price sync only (get_item()) -- deliberately separate from
# _request_with_retry() above so tuning this for price sync's real-world
# flaky-connection exposure (many client sites, some with stringent
# firewalls) can never affect order submission or any other call. Escalating
# the per-attempt timeout, rather than retrying the same timeout repeatedly,
# gives a "wifi dropped and is reconnecting" outage a real chance to recover
# within the retry window instead of just repeating an already-too-short
# timeout four times.
PRICE_REQUEST_TIMEOUTS = [15, 20, 25, 30]   # seconds, one per attempt
PRICE_RETRY_GAP_SECONDS = 2                  # flat pause between attempts


def _request_with_retry(method, url, **kwargs):
    """
    Thin wrapper around requests.get/requests.post/requests.put that adds a
    timeout and retries transient network/SSL/connection failures with
    backoff before giving up. Does NOT catch or reinterpret HTTP status
    codes or response body content -- callers still handle ResourceError /
    malformed XML / etc. exactly as they do today. Only protects against the
    underlying connection never completing at all.
    """
    l_last_exc = None
    for l_attempt in range(RETRY_ATTEMPTS):
        try:
            return method(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except RequestException as e:
            l_last_exc = e
            log_debug('Request attempt ' + str(l_attempt + 1) + '/' + str(RETRY_ATTEMPTS) + ' failed for ' + url + ': ' + str(e))
            if l_attempt < RETRY_ATTEMPTS - 1:
                sleep(RETRY_BACKOFF_SECONDS * (2 ** l_attempt))
    raise l_last_exc


def _request_with_escalating_retry(method, url, **kwargs):
    """
    Retry wrapper used ONLY by get_item() (price sync) -- see
    PRICE_REQUEST_TIMEOUTS above for why this is separate from
    _request_with_retry(). Same non-reinterpretation contract: only
    protects against the connection never completing, doesn't touch
    response content/status handling.
    """
    l_last_exc = None
    for l_attempt, l_timeout in enumerate(PRICE_REQUEST_TIMEOUTS):
        try:
            return method(url, timeout=l_timeout, **kwargs)
        except RequestException as e:
            l_last_exc = e
            log_debug(
                'Price request attempt ' + str(l_attempt + 1) + '/' + str(len(PRICE_REQUEST_TIMEOUTS)) +
                ' failed for ' + url + ' (timeout=' + str(l_timeout) + 's): ' + str(e)
            )
            if l_attempt < len(PRICE_REQUEST_TIMEOUTS) - 1:
                sleep(PRICE_RETRY_GAP_SECONDS)
    raise l_last_exc


def _request_once(method, url, **kwargs):
    """
    Single-attempt request with a timeout but deliberately NO retry -- for
    non-idempotent calls only (create_order, approve_order), where a
    connection failure after P21 has already processed the request cannot
    be told apart from a connection failure before it ever arrived.
    Retrying blindly risks re-submitting an already-created order.

    Incident, 2026-08-05: PO 1160 got three duplicate orders created in P21
    from a single script run. Root cause: _request_with_retry() doesn't
    distinguish "request never reached P21" from "P21 created the order but
    the response never made it back" -- it retried the same create_order()
    POST up to RETRY_ATTEMPTS times, and P21 apparently succeeded server-side
    on more than one of those attempts. send_erp never got set (the retry
    loop exhausted and raised, so update_order() was never reached), so the
    PO looked like a total failure locally while sitting three-times-created
    in P21.

    A single failure here surfaces as an exception straight to the caller,
    leaving erp_send_state 'inflight' and the order unconfirmed -- exactly
    the state get_stale_inflight() exists to catch and flag for a human,
    *before* a duplicate can be created rather than after.
    """
    return method(url, timeout=REQUEST_TIMEOUT, **kwargs)


def get_token():
    """Get bearer token for P21 API authentication"""
    l_headers = {"Content-Length": "0"}
    l_endpoint = (
        l_base_url +
        '/security/token/?username=' + get_api_username() +
        '&password=' + get_api_password()
    )
    return _request_with_retry(requests.post, l_endpoint, headers=l_headers).text


def get_customer_name():
    """Get customer name from P21 for email subjects"""
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Length": "0"}
    l_endpoint = l_base_url + '/entity/customers/AFI_' + get_customer_id()
    l_response = _request_with_retry(requests.get, l_endpoint, headers=l_headers).text
    try:
        l_dict = xmltodict.parse(l_response)
        l_customer = l_dict['Customer']['CustomerName']
    except xmltodict.expat.ExpatError:
        l_customer = 'Unknown'
    return l_customer.title()


def get_item(p_item):
    """Get pricing data for a single item from P21"""
    l_loc = get_location_id()
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Length": "0"}
    l_endpoint = (
        l_base_url +
        '/inventory/v2/parts/price?itemid=' + p_item +
        '&companyid=AFI' +
        '&customerid=' + get_customer_id() +
        '&saleslocid=' + l_loc +
        '&sourcelocid=' + l_loc
    )
    l_response = _request_with_escalating_retry(requests.get, l_endpoint, headers=l_headers).text
    try:
        l_dict = xmltodict.parse(l_response)
    except xmltodict.expat.ExpatError:
        l_dict = {'ResourceError': 'No Api Records'}
    return l_dict


def get_item_post(p_item, p_contract):
    """Get pricing data for a single item via POST (contract-based pricing)"""
    l_customer = get_customer_id()
    l_loc = get_location_id()
    l_xml = """
        <GetItemPrice>
          <Request>
            <B2BSellerVersion>
              <MajorVersion>23</MajorVersion>
              <MinorVersion>2</MinorVersion>
              <BuildNumber>5193</BuildNumber>
            </B2BSellerVersion>
            <ContractUID>""" + p_contract + """</ContractUID>
            <CustomerCode>""" + l_customer + """</CustomerCode>
            <StoreName>AFI</StoreName>
            <LocationID>""" + l_loc + """</LocationID>
            <ListOfItems>
              <Item>
                <ItemID>""" + p_item + """</ItemID>
                <Quantity>1</Quantity>
              </Item>
            </ListOfItems>
          </Request>
        </GetItemPrice>
    """
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = l_base_url + '/ecommerce'
    l_response = _request_with_retry(requests.post, l_endpoint, headers=l_headers, data=l_xml).text
    try:
        l_dict = xmltodict.parse(l_response)
    except xmltodict.expat.ExpatError:
        l_dict = {'ResourceError': 'No Api Records'}
    return l_dict


def create_order(p_xml):
    """
    Submit order XML to P21. Deliberately NOT retried (see _request_once) --
    this creates a real order server-side; blindly retrying a connection
    failure risks creating duplicates if P21 processed the request but the
    response was lost. A failure here is expected to propagate to the
    caller and leave the order un-cleared in erp_send_state for review.
    """
    l_data = tostring(p_xml)
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = l_base_url + '/sales/orders'
    l_response = _request_once(requests.post, l_endpoint, data=l_data, headers=l_headers).text
    try:
        l_dict = xmltodict.parse(l_response)
    except xmltodict.expat.ExpatError:
        l_dict = {'ResourceError': 'No Api Records'}
    return l_dict


def check_item_availability(p_item_ids):
    """
    Query P21 stock availability for one or more item IDs in a single batched
    call (used to diagnose why a partial order dropped specific items - see
    orders() in main.py). Returns a dict keyed by ItemId, each value the raw
    availability fields P21 returned for it (QuantityAvailable, QuantityOnHand,
    etc). An ItemId absent from the result means P21's response didn't
    include it - this function never raises, so a network/parse failure
    surfaces the same way, as a missing key, rather than crashing the
    partial-order handling that already works.
    """
    if not p_item_ids:
        return {}

    l_loc = get_location_id()
    l_items_xml = ''.join(
        '<ItemAvailabilityInfo><ItemId>' + i + '</ItemId></ItemAvailabilityInfo>'
        for i in p_item_ids
    )
    l_xml = '<ArrayOfItemAvailabilityInfo>' + l_items_xml + '</ArrayOfItemAvailabilityInfo>'
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = (
        l_base_url +
        '/inventory/parts/itemsAvailability?locationId=' + l_loc +
        '&companyId=AFI'
    )

    try:
        l_response = _request_with_retry(requests.post, l_endpoint, headers=l_headers, data=l_xml).text
        l_dict = xmltodict.parse(l_response)
    except Exception as e:
        log_error('Item availability check failed (network/parse error): ' + str(e))
        return {}

    if 'ResourceError' in l_dict:
        log_error('Item availability check returned ResourceError: ' + str(l_dict['ResourceError']))
        return {}

    l_info = (l_dict.get('ArrayOfItemAvailabilityInfo') or {}).get('ItemAvailabilityInfo') or []
    if isinstance(l_info, dict):
        l_info = [l_info]

    return {i['ItemId']: i for i in l_info if isinstance(i, dict) and 'ItemId' in i}


def get_order_status(p_orderno):
    """
    Get the current state of a previously-submitted order (used to check
    whether it's since been received/closed by P21, or is still sitting
    open -- see check_open_orders() in main.py). Response includes
    Completed/CancelledFlag/DeletedFlag at the order-header level.
    """
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Length": "0"}
    l_endpoint = l_base_url + '/sales/orders/' + p_orderno
    l_response = _request_with_retry(requests.get, l_endpoint, headers=l_headers).text
    try:
        l_dict = xmltodict.parse(l_response)
    except xmltodict.expat.ExpatError:
        l_dict = {'ResourceError': 'No Api Records'}
    return l_dict


def approve_order(p_orderno):
    """
    Approve a P21 order by order number. Deliberately NOT retried (see
    _request_once) -- same non-idempotency concern as create_order().
    """
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = l_base_url + '/sales/orders/' + p_orderno + '/approve'
    l_response = _request_once(requests.put, l_endpoint, headers=l_headers).text
    return l_response


# Fetch token once at module load time
l_token = get_token()