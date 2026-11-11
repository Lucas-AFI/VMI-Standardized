"""
Why P21 doesn't recognize an item ID, and its current ID if P21 has one --
the same resolution P21 Item Maintenance does (SCTFG125-020 -> SCT220004),
via P21's alternate codes. Read-only: changes nothing in P21 or Matrix.

Usage:
    python item_id_lookup.py SCTFG125-020 ISC12345 ...
    python item_id_lookup.py bad_items.xlsx          (item IDs in column A)
Output: item_id_lookup.csv (or <file>_new_ids.csv next to the Excel file)
with Old Item #, New Item #, New Item Description, Reason.

Only needs the P21 credentials in Windows Credential Manager. explain() is
also used by fix_item_ids.py (monthly rename) and main.items() (the reason
logged/reported for each invalid item).
"""

import csv
import sys
from os import path

import requests
from credentials import get_base_url
from price_lookup import read_column_a, p21_token

ODATA_VIEWS = get_base_url().rsplit('/api', 1)[0] + '/odataservice/odata/view/'
BATCH = 40   # values per OData filter (keeps the URL short)

REASON_DELETED = 'Marked for deletion in P21'
REASON_NOT_IN_P21 = 'Not in P21 (no item or alternate code with this ID)'


def odata(p_headers, p_view, p_filter):
    l_resp = requests.get(ODATA_VIEWS + p_view, params={'$filter': p_filter}, headers=p_headers, timeout=30)
    if l_resp.status_code != 200:
        raise RuntimeError(p_view + ': HTTP ' + str(l_resp.status_code) + ' ' + ' '.join(l_resp.text.split())[:200])
    return l_resp.json()['value']


def _batches(p_list):
    for i in range(0, len(p_list), BATCH):
        yield p_list[i:i + BATCH]


def _eq_any(p_field, p_values):
    return ' or '.join(p_field + " eq '" + str(v).replace("'", "''") + "'" for v in p_values)


def explain(p_headers, p_codes, p_progress=False):
    """
    For each code in p_codes that is NOT a live P21 item:
        {code: (new_item_id, new_item_desc, reason)}
    new_item_id is '' unless exactly one live P21 item has the code as an
    alternate code. Live codes are left out of the result.
    """
    l_by_key = {}
    for c in p_codes:
        l_by_key.setdefault(str(c).strip().upper(), []).append(c)
    l_keys = sorted(l_by_key)

    l_live, l_deleted = set(), set()
    for n, l_batch in enumerate(_batches(l_keys), 1):
        for i in odata(p_headers, 'p21_view_inv_mast', _eq_any('item_id', l_batch)):
            (l_deleted if i.get('delete_flag') == 'Y' else l_live).add(str(i['item_id']).strip().upper())
        if p_progress:
            print('\rChecked ' + str(min(n * BATCH, len(l_keys))) + '/' + str(len(l_keys)) + ' items in P21', end='', flush=True)
    if p_progress and l_keys:
        print()
    l_bad = [k for k in l_keys if k not in l_live]

    l_alt = {}
    for l_batch in _batches(l_bad):
        for a in odata(p_headers, 'p21_view_alternate_code', _eq_any('alternate_code', l_batch)):
            if a.get('delete_flag') != 'Y':
                l_alt.setdefault(str(a['alternate_code']).strip().upper(), set()).add(a['inv_mast_uid'])

    l_items = {}
    for l_batch in _batches(sorted({u for s in l_alt.values() for u in s})):
        for i in odata(p_headers, 'p21_view_inv_mast', ' or '.join('inv_mast_uid eq ' + str(u) for u in l_batch)):
            l_items[i['inv_mast_uid']] = i

    l_result = {}
    for k in l_bad:
        l_targets = [l_items[u] for u in sorted(l_alt.get(k, ())) if u in l_items]
        l_live_targets = [t for t in l_targets if t.get('delete_flag') != 'Y']
        if len(l_live_targets) == 1:
            t = l_live_targets[0]
            r = (t['item_id'], t.get('item_desc') or '', 'Replaced in P21 by ' + t['item_id'])
        elif l_live_targets:
            r = ('', '', 'Matches more than one P21 item: ' + ', '.join(t['item_id'] for t in l_live_targets))
        elif l_targets:
            r = ('', '', 'Replaced in P21 by ' + ', '.join(t['item_id'] for t in l_targets) +
                 ', which is also marked for deletion')
        elif k in l_deleted:
            r = ('', '', REASON_DELETED)
        else:
            r = ('', '', REASON_NOT_IN_P21)
        for c in l_by_key[k]:
            l_result[c] = r
    return l_result


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
    l_found = explain(l_headers, l_ids)
    l_resolved = 0
    with open(l_out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Old Item #', 'New Item #', 'New Item Description', 'Reason'])
        for l_old in l_ids:
            l_new, l_desc, l_reason = l_found.get(l_old, ('', '', 'Already a live P21 item -- nothing to replace'))
            l_resolved += bool(l_new)
            w.writerow([l_old, l_new, l_desc, l_reason])
            print(l_old + ' -> ' + (l_new or '(' + l_reason + ')'))

    print('\n' + str(l_resolved) + ' of ' + str(len(l_ids)) + ' resolved. Wrote ' + path.abspath(l_out))


if __name__ == '__main__':
    main()
