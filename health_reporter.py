"""
VMI Health Dashboard - Local Reporter

Standalone script. Deployed alongside update_process but scheduled
INDEPENDENTLY (its own Task Scheduler entry, every 15 minutes) so it keeps
reporting even if update_process itself hangs, crashes, or its own
scheduled task silently stops running (e.g. a Python path / Task Scheduler
breakage).

Responsibilities each run:
  1. Read unreported events + last-run status from the local health_state.db
     (written to by update_process via health.py)
  2. Determine current_status: 'operational' or 'error'
  3. POST a small JSON payload to the central Azure endpoint
  4. On success, mark events as reported so they aren't sent twice

This script deliberately does NOT import anything from main.py/db.py/api.py
so that a bug in update_process can never take the reporter down with it.
It opens its own throwaway DB connection only for the (approximate) stale
in-flight check below.

Config (config.ini, [health] section):
    client_name       : human-readable name for the dashboard (e.g. "American Torch Tip")
    endpoint_url       : central Azure intake URL
    reporter_secret    : shared secret, stored via keyring (see setup_health.py)
Falls back to [DEFAULT]/sql_* config for the stale-order approximation.
"""

import json
import sqlite3
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import config          # existing standardized-repo module: reads config.ini
import credentials      # existing standardized-repo module: keyring wrapper
from db import connect_db, close_db_conn  # read-only use here

l_health_db = 'health_state.db'
l_reporter_log = 'logs/health_reporter.log'

# How long a PO can sit in erp_send_state with status = 'inflight' before we
# treat it as stuck (crash/lost connection mid-submission). Kept in sync with
# the 1-hour threshold db.py's get_stale_inflight() uses on the main.py side
# -- both read the same erp_send_state table, just from separate processes.
STALE_ORDER_HOURS = 1


def _log(p_line):
    with open(l_reporter_log, 'a') as f:
        f.write(datetime.now(timezone.utc).isoformat() + ' - ' + p_line + '\n')


def get_unreported_events():
    l_conn = sqlite3.connect(l_health_db, timeout=10)
    l_conn.execute(
        'CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'event_type TEXT, detail TEXT, po_code TEXT, created_at TEXT, reported INTEGER DEFAULT 0)'
    )
    l_rows = l_conn.execute(
        'SELECT id, event_type, detail, po_code, created_at FROM events WHERE reported = 0'
    ).fetchall()
    l_conn.close()
    return l_rows


def mark_events_reported(p_ids):
    if not p_ids:
        return
    l_conn = sqlite3.connect(l_health_db, timeout=10)
    l_conn.executemany('UPDATE events SET reported = 1 WHERE id = ?', [(i,) for i in p_ids])
    l_conn.commit()
    l_conn.close()


def get_run_log():
    l_conn = sqlite3.connect(l_health_db, timeout=10)
    l_conn.execute(
        'CREATE TABLE IF NOT EXISTS run_log (run_type TEXT PRIMARY KEY, last_run_at TEXT, '
        'last_status TEXT, succ_cnt INTEGER, tot_cnt INTEGER, err_cnt INTEGER)'
    )
    try:
        # Same pre-existing-database patch as health.py's _connect() -- this
        # script deliberately keeps its own schema handling rather than
        # importing from health.py (see module docstring).
        l_conn.execute('ALTER TABLE run_log ADD COLUMN err_cnt INTEGER')
    except sqlite3.OperationalError:
        pass
    l_rows = l_conn.execute('SELECT run_type, last_run_at, last_status, succ_cnt, tot_cnt, err_cnt FROM run_log').fetchall()
    l_conn.close()
    return {r[0]: {'last_run_at': r[1], 'last_status': r[2], 'succ_cnt': r[3], 'tot_cnt': r[4], 'err_cnt': r[5]} for r in l_rows}


def get_stale_pending_orders():
    """
    Flags PO codes whose submission has been sitting in erp_send_state with
    status = 'inflight' for longer than STALE_ORDER_HOURS -- i.e. the script
    started submitting them to P21 but crashed or lost the connection before
    the outcome was recorded. See mark_inflight()/clear_inflight()/
    get_stale_inflight() in db.py, and sql/erp_send_state.sql for the table
    itself.

    Machines that don't have the erp_send_state table yet (not all ~150
    sites have this migration applied) will simply fail this query and
    return [] -- this degrades safely and does not block the rest of the
    health report.
    """
    try:
        l_conn = connect_db()
        l_cursor = l_conn.cursor()
        l_cursor.execute(
            "SELECT h.po_code FROM dbo.erp_send_state s "
            "JOIN dbo.ent_po_headers h ON h.po_key = s.po_key "
            "WHERE s.status = 'inflight' "
            "AND s.updated_at < DATEADD(hour, -?, GETDATE())",
            (STALE_ORDER_HOURS,)
        )
        l_rows = [r[0] for r in l_cursor.fetchall()]
        close_db_conn(l_conn)
        return l_rows
    except Exception as e:
        _log('Stale in-flight check skipped (erp_send_state not present on this machine yet?): ' + str(e))
        return []


def build_payload():
    l_run_log = get_run_log()
    l_events = get_unreported_events()
    l_stale = get_stale_pending_orders()

    l_event_dicts = [
        {
            'event_id': r[0],
            'event_type': r[1],
            'detail': r[2],
            'po_code': r[3],
            'detected_at': r[4],
        }
        for r in l_events
    ]

    l_has_error = (
        any(e['event_type'] in ('order_error', 'partial_order', 'db_error', 'api_error', 'run_failure') for e in l_event_dicts)
        or any(r.get('last_status') == 'error' for r in l_run_log.values())
        or len(l_stale) > 0
    )

    l_payload = {
        'client_name': config.get_health_client_name(),
        'reported_at': datetime.now(timezone.utc).isoformat(),
        'current_status': 'error' if l_has_error else 'operational',
        'run_log': l_run_log,
        'events': l_event_dicts,
        'stale_pending_orders': l_stale,
    }
    return l_payload, [e['event_id'] for e in l_event_dicts]


def send_payload(p_payload):
    l_url = config.get_health_endpoint_url()
    l_secret = credentials.get_health_reporter_secret()

    l_data = json.dumps(p_payload).encode('utf-8')
    l_req = Request(
        l_url,
        data=l_data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + l_secret,
        },
        method='POST',
    )
    with urlopen(l_req, timeout=15) as l_resp:
        return l_resp.status


def main():
    try:
        l_payload, l_event_ids = build_payload()
        l_status = send_payload(l_payload)
        if 200 <= l_status < 300:
            mark_events_reported(l_event_ids)
            _log('Reported status=' + l_payload['current_status'] + ', ' + str(len(l_event_ids)) + ' event(s) cleared.')
        else:
            _log('Endpoint returned non-2xx status: ' + str(l_status) + ' -- events left unmarked, will retry next run.')
    except (URLError, HTTPError) as e:
        _log('Network/endpoint error, will retry next run: ' + str(e))
    except Exception as e:
        _log('Unexpected reporter error: ' + str(e))


if __name__ == '__main__':
    main()
