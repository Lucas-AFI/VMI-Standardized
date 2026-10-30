"""
One-off P21 price lookup: item IDs from column A of an .xlsx -> CSV of prices
for any customer. Doesn't touch the Matrix DB or config.ini -- only needs the
P21 credentials in Windows Credential Manager (same ones Price Sync uses).

Usage:
    python price_lookup.py items.xlsx
    python price_lookup.py items.xlsx --customer 12345 --location 10 --ship-to 2
Missing --customer is prompted for. Output: items_prices_<customer>.csv next
to the input file.
"""

import argparse
import csv
import re
from os import path
from zipfile import ZipFile
from xml.etree import ElementTree

import requests
import xmltodict
from credentials import get_base_url, get_api_username, get_api_password

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def read_column_a(p_file):
    #Column A of the first sheet, stdlib only (openpyxl isn't installed fleet-wide)
    with ZipFile(p_file) as z:
        l_shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ElementTree.fromstring(z.read('xl/sharedStrings.xml')).iter(NS + 'si'):
                l_shared.append(''.join(t.text or '' for t in si.iter(NS + 't')))
        l_sheet = ElementTree.fromstring(z.read('xl/worksheets/sheet1.xml'))

    l_items = []
    for c in l_sheet.iter(NS + 'c'):
        if not re.fullmatch(r'A\d+', c.get('r', '')):
            continue
        l_t = c.get('t')
        if l_t == 'inlineStr':
            l_val = ''.join(t.text or '' for t in c.iter(NS + 't'))
        else:
            v = c.find(NS + 'v')
            if v is None:
                continue
            l_val = l_shared[int(v.text)] if l_t == 's' else v.text
            if l_t is None and l_val.endswith('.0'):
                l_val = l_val[:-2]  # numeric item IDs come back as "12345.0"
        if l_val and l_val.strip():
            l_items.append(l_val.strip())
    return l_items


def main():
    l_args = argparse.ArgumentParser()
    l_args.add_argument('file')
    l_args.add_argument('--customer')
    l_args.add_argument('--location', default='10')
    l_args.add_argument('--ship-to')
    l_args = l_args.parse_args()
    l_customer = l_args.customer or input('P21 customer ID: ').strip()

    l_items = read_column_a(l_args.file)
    if l_items and not any(ch.isdigit() for ch in l_items[0]):
        l_items = l_items[1:]  # skip a header like "Item ID"
    print(str(len(l_items)) + ' items, customer ' + l_customer)

    l_base = get_base_url()
    l_token = requests.post(
        l_base + '/security/token/?username=' + get_api_username() + '&password=' + get_api_password(),
        headers={'Content-Length': '0'}, timeout=30)
    if l_token.status_code != 200:
        raise SystemExit('P21 token request failed: HTTP ' + str(l_token.status_code) + ' -- check P21 API Base URL')
    l_headers = {'Authorization': 'Bearer ' + l_token.text, 'Content-Length': '0'}

    l_out = path.splitext(l_args.file)[0] + '_prices_' + l_customer + '.csv'
    with open(l_out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Item #', 'Unit Price', 'Note'])
        for i, l_item in enumerate(l_items, 1):
            l_url = (l_base + '/inventory/v2/parts/price?itemid=' + requests.utils.quote(l_item) +
                     '&companyid=AFI&customerid=' + l_customer +
                     '&saleslocid=' + l_args.location + '&sourcelocid=' + l_args.location)
            if l_args.ship_to:
                l_url += '&shiptoid=' + l_args.ship_to
            try:
                l_resp = xmltodict.parse(requests.get(l_url, headers=l_headers, timeout=30).text)
                l_price = (l_resp.get('ItemPrice') or {}).get('UnitPrice')
                if l_price is not None:
                    w.writerow([l_item, '{:.4f}'.format(float(l_price)), ''])
                else:
                    l_err = l_resp.get('ResourceError')
                    l_msg = l_err.get('ErrorMessage', '') if isinstance(l_err, dict) else str(l_err or l_resp)
                    w.writerow([l_item, '', l_msg[:200]])
            except Exception as e:
                w.writerow([l_item, '', 'Request failed: ' + str(e)[:200]])
            print('\r' + str(i) + '/' + str(len(l_items)), end='', flush=True)

    print('\nWrote ' + l_out)


if __name__ == '__main__':
    main()
