"""
VMI Update Process - XML Order Builder

Machine-specific environment variables:
    P21_CUSTOMER_ID : AFI customer ID for this Matrix machine
    P21_SHIP_TO_ID  : Optional ship-to ID. If set, included in order XML.
                      Required when customer has multiple ship-to addresses in P21.
"""

import xml.etree.ElementTree as et
from os import environ


def build_order(p_order, p_quote=None):
    """
    Build P21 order XML structure.
    Includes ShipToId only if P21_SHIP_TO_ID environment variable is set.
    """
    root = et.Element('Order')

    customer_id = et.SubElement(root, 'CustomerId')
    customer_id.text = str(environ['P21_CUSTOMER_ID'])

    po_no = et.SubElement(root, 'PoNo')
    po_no.text = p_order.po_code

    l_ship_to = environ.get('P21_SHIP_TO_ID')
    if l_ship_to:
        ship_to = et.SubElement(root, 'ShipToId')
        ship_to.text = str(l_ship_to)

    if p_quote:
        quote = et.SubElement(root, 'Quote')
        quote.text = 'Y'

    return root


def add_line_item(p_order, p_order_items):
    """Add order line items to XML structure"""
    root = et.SubElement(p_order, 'Lines')
    l_item_ids = []

    for l_order_item in p_order_items:
        order_item = et.SubElement(root, 'OrderLine')

        line_no = et.SubElement(order_item, 'LineNo')
        line_no.text = str(l_order_item.po_line_no)

        item_id = et.SubElement(order_item, 'ItemId')
        item_id.text = l_order_item.item_code

        qty = et.SubElement(order_item, 'UnitQuantity')
        qty.text = str(l_order_item.qty)

        l_item_ids.append(item_id.text)

    return p_order, l_item_ids


def print_xml(p_xml, p_name=None):
    """Print or save XML for debugging"""
    if isinstance(p_xml, str):
        l_xml = et.fromstring(p_xml)
        et.dump(l_xml)
    else:
        et.indent(p_xml, space='  ', level=0)
        if p_name:
            l_xml = et.tostring(p_xml)
            with open(p_name, "wb") as f:
                f.write(l_xml)
            return
        et.dump(p_xml)


def tostring(p_xml):
    """Convert XML element to bytes with declaration"""
    return b"<?xml version='1.0' ?>" + et.tostring(p_xml)


def fromstring(p_xml):
    """Parse XML string to element"""
    return et.fromstring(p_xml)
