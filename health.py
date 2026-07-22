"""
VMI Update Process - Local Health Event Queue

Lightweight, dependency-free (stdlib sqlite3) local store that update_process
writes to at key moments, and that health_reporter.py drains on its own
15-minute schedule and POSTs to the central dashboard endpoint.

This module is intentionally isolated from main.py/db.py's own logging
(log.py / app.log) -- it exists purely so a separate, independent process
(health_reporter.py) can observe what happened without parsing log text.

Local file: health_state.db (SQLite, same folder as the script; gitignored
alongside config.ini since its content is machine-specific and ephemeral).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

l_db_location = 'health_state.db'


def _connect():
    l_conn = sqlite3.connect(l_db_location, timeout=10)
    l_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            detail TEXT,
            po_code TEXT,
            created_at TEXT NOT NULL,
            reported INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    l_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_log (
            run_type TEXT PRIMARY KEY,
            last_run_at TEXT NOT NULL,
            last_status TEXT NOT NULL,
            succ_cnt INTEGER,
            tot_cnt INTEGER
        )
        """
    )
    return l_conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def record_event(p_event_type, p_detail=None, p_po_code=None):
    """
    Record a structured health event.

    p_event_type: one of
        'order_error'    - check_order() returned 'error' (nothing submitted)
        'partial_order'  - check_order() returned 'partial' (items silently dropped)
        'db_error'       - controlled_exit() was triggered (DB/connection failure)
        'api_error'      - unhandled exception calling P21 API
        'run_failure'    - items()/orders() crashed somewhere not covered by
                            the four types above; p_detail is the traceback
                            (see main.py's outer except Exception blocks).
                            record_run() alone only tracks the latest
                            pass/fail flag and gets silently overwritten by
                            the next successful run -- this is what leaves a
                            durable, dashboard-visible trace of what broke.
    p_detail: free-text detail/summary (e.g. the check_order() message)
    p_po_code: related PO code, if applicable
    """
    try:
        l_conn = _connect()
        l_conn.execute(
            'INSERT INTO events (event_type, detail, po_code, created_at, reported) '
            'VALUES (?, ?, ?, ?, 0)',
            (p_event_type, p_detail, p_po_code, _now())
        )
        l_conn.commit()
        l_conn.close()
    except Exception:
        # Health tracking must never break the actual update_process run.
        # If this fails, the missed event will simply not appear on the
        # dashboard -- swallow it rather than raise.
        pass


def event_already_recorded(p_event_type, p_po_code):
    """
    True if an event of this type/PO combination has ever been recorded
    locally, regardless of whether it has since been reported to the
    dashboard -- used for event types that should be recorded once per PO
    rather than every cycle it remains true (e.g. 'stale_inflight_order' in
    main.py's orders(), which would otherwise get a fresh row every run
    while a PO stays stuck).
    """
    try:
        l_conn = _connect()
        l_row = l_conn.execute(
            'SELECT 1 FROM events WHERE event_type = ? AND po_code = ? LIMIT 1',
            (p_event_type, p_po_code)
        ).fetchone()
        l_conn.close()
        return l_row is not None
    except Exception:
        return False


def record_run(p_run_type, p_status, p_succ_cnt=None, p_tot_cnt=None):
    """
    Record that a run of 'items' or 'orders' completed, and how it went.
    p_status: 'success' or 'error'
    """
    try:
        l_conn = _connect()
        l_conn.execute(
            'INSERT INTO run_log (run_type, last_run_at, last_status, succ_cnt, tot_cnt) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(run_type) DO UPDATE SET '
            'last_run_at=excluded.last_run_at, last_status=excluded.last_status, '
            'succ_cnt=excluded.succ_cnt, tot_cnt=excluded.tot_cnt',
            (p_run_type, _now(), p_status, p_succ_cnt, p_tot_cnt)
        )
        l_conn.commit()
        l_conn.close()
    except Exception:
        pass
