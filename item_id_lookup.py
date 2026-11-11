"""
Look up the current P21 item ID for old/superseded item IDs -- the same
resolution P21 Item Maintenance does (SCTFG125-020 -> SCT220004), via P21's
alternate codes. Read-only: changes nothing in P21 or Matrix.

Usage:
    python item_id_lookup.py SCTFG125-020 ISC12345 ...
    python item_id_lookup.py bad_items.xlsx          (item IDs in column A)
Output: item_id_lookup.csv (or <file>_new_ids.csv next to the Excel file)
with Old Item #, New Item #, New Item Description, Note.

Only needs the P21 credentials in Windows Credential Manager.
"""

import csv
import sys
from os import path

import requests
from credentials import get_base_url
from price_lookup import read_column_a, p21_token

ODATA_VIEWS = get_base_url().rsplit('/api', 1)[0] + '/odataservice/odata/view/'


def odata(p_headers, p_view, p_filter):
    l_resp = requests.get(ODATA_VIEWS + p_view, params={'$filter': p_filter}, headers=p_headers, timeout=30)
    if l_resp.status_code != 200:
        raise RuntimeError(p_view + ': HTTP ' + str(l_resp.status_code) + ' ' + ' '.join(l_resp.text.split())[:200])
    return l_resp.json()['value']


def lookup(p_headers, p_old_id):
    # Returns (new_item_id, new_item_desc, note). new_item_id is '' unless
    # exactly one live P21 item has p_old_id as an alternate code.
    l_quoted = p_old_id.replace("'", "''")
    l_alts = odata(p_headers, 'p21_view_alternate_code', "alternate_code eq '" + l_quoted + "'")
    l_uids = sorted({a['inv_mast_uid'] for a in l_alts if a.get('delete_flag') != 'Y'})
    if not l_uids:
        return '', '', 'No alternate code in P21 -- needs manual lookup'

    l_items = odata(p_headers, 'p21_view_inv_mast', ' or '.join('inv_mast_uid eq ' + str(u) for u in l_uids))
    l_live = [i for i in l_items if i.get('delete_flag') != 'Y']
    if len(l_live) == 1:
        return l_live[0]['item_id'], l_live[0].get('item_desc') or '', ''
    if not l_live:
        return '', '', 'Replacement item is deleted in P21'
    return '', '', 'Matches more than one P21 item: ' + ', '.join(i['item_id'] for i in l_live)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    if sys.argv[1].lower().endswith('.xlsx'):
        l_ids = read_column_a(sys.argv[1])
        if l_ids and not any(ch.isdigit() for ch in l_ids[0]):
            l_ids = l_ids[1:]  # skip a header like "Item ID"
        l_out = path.splitext(sys.argv[1])[0] + '_new_ids.csv'
    else:
        l_ids = sys.argv[1:]
        l_out = 'item_id_lookup.csv'

    l_headers = {'Authorization': 'Bearer ' + p21_token(), 'Accept': 'application/json'}
    l_found = 0
    with open(l_out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Old Item #', 'New Item #', 'New Item Description', 'Note'])
        for l_old in l_ids:
            try:
                l_new, l_desc, l_note = lookup(l_headers, l_old)
            except Exception as e:
                l_new, l_desc, l_note = '', '', 'Lookup failed: ' + str(e)[:200]
            l_found += bool(l_new)
            w.writerow([l_old, l_new, l_desc, l_note])
            print(l_old + ' -> ' + (l_new or '(' + l_note + ')'))

    print('\n' + str(l_found) + ' of ' + str(len(l_ids)) + ' resolved. Wrote ' + path.abspath(l_out))


if __name__ == '__main__':
    main()
