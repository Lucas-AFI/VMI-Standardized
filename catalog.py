"""
VMI Update Process - Item Catalog Sync

Pushes the active AFI-supplied item catalog (key, code, description, type,
pack size, price) to the central Azure health dashboard. Reuses the same
shared secret as health_reporter.py (HEALTH_REPORTER_SECRET via keyring) --
no new credential needed, just a distinct payload and a distinct intake
endpoint (catalog_endpoint_url, [health] section in config.ini) from the
15-minute health heartbeat.

Deliberately its own action (`-a catalog`), not folded into
health_reporter.py: catalog sync wants its own cadence (daily/weekly via its
own Task Scheduler entry, not every 15 minutes) and carries a much larger
payload than health_reporter.py's small heartbeat. Keeping it separate means
a slow query or a large POST here can never cause the heartbeat itself to
miss a beat.

sync_catalog() matches items()/orders()/sync_images() in shape: logging,
health reporting, log rotation.
"""

import traceback
import requests
from config import get_health_client_name, get_catalog_endpoint_url
from credentials import get_health_reporter_secret
from db import connect_db, close_db_conn, get_catalog_items
from log import log_debug, log_error, start_log, stop_log
from utils import rename_log
import health

# Single attempt, generous timeout -- the payload here is the full item
# catalog (potentially thousands of rows), unlike health_reporter.py's small
# heartbeat, so it needs more margin than a quick status ping would.
CATALOG_REQUEST_TIMEOUT = 30


def build_catalog_payload(p_rows):
    return {
        'client_name': get_health_client_name(),
        'items': [
            {
                'item_key': row.item_key,
                'item_code': row.item_code,
                'item_description': row.item_description,
                'item_type': row.item_type,
                'pack_size': row.packet_size,
                'item_price': float(row.item_price) if row.item_price is not None else None,
            }
            for row in p_rows
        ],
    }


def sync_catalog():
    # Push the active item catalog to the central Azure health dashboard
    l_tot_cnt = 0

    start_log('Catalog sync process')

    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()
        l_rows = get_catalog_items(l_cursor)
        close_db_conn(l_db_conn)

        l_tot_cnt = len(l_rows)
        l_payload = build_catalog_payload(l_rows)

        l_headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + get_health_reporter_secret(),
        }
        l_response = requests.post(
            get_catalog_endpoint_url(), json=l_payload, headers=l_headers, timeout=CATALOG_REQUEST_TIMEOUT
        )

        if l_response.status_code != 200:
            raise Exception('Catalog endpoint returned HTTP ' + str(l_response.status_code) + ': ' + l_response.text)

        log_debug('Catalog synced: ' + str(l_tot_cnt) + ' item(s)')
        stop_log('Catalog sync process', l_tot_cnt, l_tot_cnt)

        health.record_run('catalog', 'success', l_tot_cnt, l_tot_cnt)

        rename_log()
    except Exception:
        l_traceback = traceback.format_exc()
        log_error('Unhandled exception in sync_catalog():\n' + l_traceback)
        health.record_event('run_failure', l_traceback)
        health.record_run('catalog', 'error', 0, l_tot_cnt)
        raise
