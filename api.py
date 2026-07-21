"""
VMI Update Process - AFI P21 API Integration

API credentials are stored in Windows Credential Manager via collect_config.py.
Machine-specific settings are read from config.ini via config.py.
"""

import requests
import xmltodict
from xml_processor import tostring
from credentials import get_base_url, get_api_username, get_api_password
from config import get_customer_id, get_location_id
from log import log_error

l_base_url = get_base_url()


def get_token():
    """Get bearer token for P21 API authentication"""
    l_headers = {"Content-Length": "0"}
    l_endpoint = (
        l_base_url +
        '/security/token/?username=' + get_api_username() +
        '&password=' + get_api_password()
    )
    return requests.post(l_endpoint, headers=l_headers).text


def get_customer_name():
    """Get customer name from P21 for email subjects"""
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Length": "0"}
    l_endpoint = l_base_url + '/entity/customers/AFI_' + get_customer_id()
    l_response = requests.get(l_endpoint, headers=l_headers).text
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
    l_response = requests.get(l_endpoint, headers=l_headers).text
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
    l_response = requests.post(l_endpoint, headers=l_headers, data=l_xml).text
    try:
        l_dict = xmltodict.parse(l_response)
    except xmltodict.expat.ExpatError:
        l_dict = {'ResourceError': 'No Api Records'}
    return l_dict


def create_order(p_xml):
    """Submit order XML to P21"""
    l_data = tostring(p_xml)
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = l_base_url + '/sales/orders'
    l_response = requests.post(l_endpoint, data=l_data, headers=l_headers).text
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
        l_response = requests.post(l_endpoint, headers=l_headers, data=l_xml).text
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


def approve_order(p_orderno):
    """Approve a P21 order by order number"""
    l_headers = {"Authorization": "Bearer " + l_token, "Content-Type": "application/xml"}
    l_endpoint = l_base_url + '/sales/orders/' + p_orderno + '/approve'
    l_response = requests.put(l_endpoint, headers=l_headers).text
    return l_response


# Fetch token once at module load time
l_token = get_token()