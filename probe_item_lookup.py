"""
One-off probe: which P21 API calls (if any) resolve an old/alternate item ID
to its current one, the way P21 Item Maintenance does. GET requests only --
changes nothing. Prints each URL's HTTP status and the start of its reply.

Usage (from the VMI folder, on a machine with P21 credentials):
    python probe_item_lookup.py SCTFG125-020 SCT220004
"""

import sys
from urllib.parse import quote

import requests
from api import l_base_url, l_token

l_old, l_new = sys.argv[1], sys.argv[2]
l_root = l_base_url.rsplit('/api', 1)[0]
l_headers = {'Authorization': 'Bearer ' + l_token, 'Accept': 'application/xml'}

l_urls = [
    l_base_url + '/inventory/parts/' + quote(l_old),
    l_base_url + '/inventory/parts/' + quote(l_new),
    l_base_url + "/inventory/parts?$query=ItemId eq '" + quote(l_old) + "'",
    l_base_url + '/inventory/parts?search=' + quote(l_old),
    l_root + "/odataservice/odata/view/p21_view_alternate_code?$filter=alternate_code eq '" + quote(l_old) + "'",
    l_root + "/odataservice/odata/table/alternate_code?$filter=alternate_code eq '" + quote(l_old) + "'",
    l_root + "/data/erp/views/v1/p21_view_alternate_code?$filter=alternate_code eq '" + quote(l_old) + "'",
    l_base_url + '/help',
    l_root + '/api/swagger',
]

for l_url in l_urls:
    try:
        l_resp = requests.get(l_url, headers=l_headers, timeout=20)
        l_text = ' '.join(l_resp.text.split())[:400]
        print('\n[' + str(l_resp.status_code) + '] ' + l_url + '\n    ' + l_text)
    except Exception as e:
        print('\n[FAILED] ' + l_url + '\n    ' + type(e).__name__)
