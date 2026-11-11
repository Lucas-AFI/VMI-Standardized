"""
Find every active AFI item in this Matrix DB whose item code P21 no longer
recognizes, look up its current P21 ID (via P21 alternate codes, see
item_id_lookup.py), and rename the Matrix item code to it.

Usage (from the VMI folder):
    python fix_item_ids.py            report only -- changes nothing
    python fix_item_ids.py --apply    rename every 'ready' item
    python fix_item_ids.py --undo     reverse the most recent --apply run
Monthly scheduled task: python main.py -a item_ids (same as --apply, plus
log/email/health -- see item_ids() in main.py).

Report: logs/item_ids_<date>_<time>.xlsx (every run, scheduled or manual). Status values:
    ready      -- one clear replacement, safe to rename
    updated    -- renamed (--apply)
    conflict   -- new ID is already a Matrix item code, two old codes map to
                  it, or it's too long for Matrix; a person decides
    no match   -- P21 has no replacement on file; Reason says why (e.g.
                  marked for deletion in P21, or not in P21 at all)
    error      -- rename failed; rerun

Only ENT_ITEM_MASTER.ITEM_CODE changes. Matrix links POs, stock, etc. by
item_key, so history stays attached. Every rename is logged in
dbo.vmi_item_code_changes (created on first --apply), which --undo reads.
"""

import argparse
from collections import Counter
from datetime import datetime

import log
from db import connect_db, close_db_conn, get_items
from item_id_lookup import explain
from price_lookup import p21_token
from utils import build_xlsx

LOG_TABLE = 'dbo.vmi_item_code_changes'
HEADER = ['Item Key', 'Old Item #', 'New Item #', 'New Item Description', 'Status', 'Reason']


def scan(l_cursor, p_progress=True):
    """Rows of HEADER for every AFI item P21 doesn't recognize. Reads only."""
    l_items = {}
    for r in get_items(l_cursor):
        l_items.setdefault(r.item_key, str(r.item_code).strip())
    l_cursor.execute('select item_code from dbo.ent_item_master')
    l_all_matrix_codes = {str(r.item_code).strip().upper() for r in l_cursor.fetchall() if r.item_code}
    l_cursor.execute(
        "select character_maximum_length from information_schema.columns "
        "where table_name = 'ENT_ITEM_MASTER' and column_name = 'ITEM_CODE'"
    )
    l_max_len = (l_cursor.fetchone() or [30])[0] or 30

    l_headers = {'Authorization': 'Bearer ' + p21_token(), 'Accept': 'application/json'}
    l_found = explain(l_headers, sorted(set(l_items.values())), p_progress)

    l_rows = []
    for l_key, l_old in l_items.items():
        if l_old in l_found:
            l_new, l_desc, l_reason = l_found[l_old]
            l_rows.append([l_key, l_old, l_new, l_desc, 'ready' if l_new else 'no match', l_reason])

    # A rename must not collide with an existing Matrix item code or with
    # another rename in this same batch, and must fit the column.
    l_new_counts = Counter(r[2].upper() for r in l_rows if r[4] == 'ready')
    for r in l_rows:
        if r[4] != 'ready':
            continue
        if r[2].upper() in l_all_matrix_codes:
            r[4], r[5] = 'conflict', r[2] + ' already exists as a Matrix item code'
        elif l_new_counts[r[2].upper()] > 1:
            r[4], r[5] = 'conflict', 'More than one old item maps to ' + r[2]
        elif len(r[2]) > l_max_len:
            r[4], r[5] = 'conflict', r[2] + ' is longer than Matrix allows (' + str(l_max_len) + ' characters)'
    return sorted(l_rows, key=lambda r: (r[4], r[1]))


def _ensure_log_table(l_cursor):
    l_cursor.execute(
        "if object_id('" + LOG_TABLE + "') is null "
        "create table " + LOG_TABLE + " ("
        "id int identity primary key, "
        "run_id varchar(14) not null, "
        "item_key int not null, "
        "old_code nvarchar(100) not null, "
        "new_code nvarchar(100) not null, "
        "changed_at datetime not null default getdate(), "
        "undone_at datetime null)"
    )


def apply_changes(l_conn, p_rows):
    """Rename every 'ready' row (status becomes 'updated' or 'error').
    One transaction per item: the rename and its log row land together."""
    l_ready = [r for r in p_rows if r[4] == 'ready']
    if not l_ready:
        return 0
    l_cursor = l_conn.cursor()
    _ensure_log_table(l_cursor)   # fails before any rename if we can't log
    l_run_id = datetime.now().strftime('%Y%m%d%H%M%S')
    l_done = 0

    l_conn.autocommit = False
    try:
        for r in l_ready:
            l_key, l_old, l_new = r[0], r[1], r[2]
            try:
                l_cursor.execute(
                    'update dbo.ent_item_master set item_code = ? '
                    'where item_key = ? and ltrim(rtrim(item_code)) = ?',
                    l_new, l_key, l_old
                )
                if l_cursor.rowcount != 1:
                    l_conn.rollback()
                    r[4], r[5] = 'error', 'Item changed in Matrix since the scan -- not renamed'
                    continue
                l_cursor.execute(
                    'insert into ' + LOG_TABLE + ' (run_id, item_key, old_code, new_code) values (?, ?, ?, ?)',
                    l_run_id, l_key, l_old, l_new
                )
                l_conn.commit()
                r[4] = 'updated'
                l_done += 1
            except Exception as e:
                l_conn.rollback()
                r[4], r[5] = 'error', 'Rename failed: ' + str(e)[:200]
    finally:
        l_conn.autocommit = True
    return l_done


def undo_last(l_conn):
    """Reverse the most recent run that hasn't been undone. Returns
    (run_id, reverted, skipped) -- skipped = item code changed again since."""
    l_cursor = l_conn.cursor()
    _ensure_log_table(l_cursor)
    l_cursor.execute('select max(run_id) from ' + LOG_TABLE + ' where undone_at is null')
    l_run_id = l_cursor.fetchone()[0]
    if not l_run_id:
        return None, 0, 0
    l_cursor.execute('select id, item_key, old_code, new_code from ' + LOG_TABLE +
                     ' where run_id = ? and undone_at is null', l_run_id)
    l_reverted = l_skipped = 0
    for l_id, l_key, l_old, l_new in l_cursor.fetchall():
        l_cursor.execute('update dbo.ent_item_master set item_code = ? where item_key = ? and item_code = ?',
                         l_old, l_key, l_new)
        if l_cursor.rowcount == 1:
            l_cursor.execute('update ' + LOG_TABLE + ' set undone_at = getdate() where id = ?', l_id)
            l_reverted += 1
        else:
            l_skipped += 1
    return l_run_id, l_reverted, l_skipped


def report_xlsx(p_rows):
    return build_xlsx([('Item ID Changes', HEADER, p_rows)])


def save_report(p_rows):
    # Dated copy in logs\ (gitignored) -- kept even if the email fails, and
    # never overwritten by the next run. Returns (path, xlsx bytes).
    l_xlsx = report_xlsx(p_rows)
    log.l_log_dir.mkdir(exist_ok=True)
    l_path = str(log.l_log_dir / ('item_ids_' + datetime.now().strftime('%Y_%m_%d_%H%M%S') + '.xlsx'))
    with open(l_path, 'wb') as f:
        f.write(l_xlsx)
    return l_path, l_xlsx


def main():
    l_args = argparse.ArgumentParser(description='Find and rename Matrix item codes P21 has replaced.')
    l_args.add_argument('--apply', action='store_true', help="rename every 'ready' item")
    l_args.add_argument('--undo', action='store_true', help='reverse the most recent --apply run')
    l_args = l_args.parse_args()

    l_conn = connect_db()
    try:
        if l_args.undo:
            l_run_id, l_reverted, l_skipped = undo_last(l_conn)
            if not l_run_id:
                print('Nothing to undo.')
            else:
                print('Run ' + l_run_id + ': ' + str(l_reverted) + ' item code(s) restored' +
                      (', ' + str(l_skipped) + ' skipped (changed again since)' if l_skipped else '') + '.')
            return

        l_rows = scan(l_conn.cursor())
        if l_args.apply:
            apply_changes(l_conn, l_rows)
    finally:
        close_db_conn(l_conn)

    l_out, _ = save_report(l_rows)

    l_status = Counter(r[4] for r in l_rows)
    print(str(len(l_rows)) + ' item(s) not recognized by P21:')
    for s in ('updated', 'ready', 'conflict', 'no match', 'error'):
        if l_status.get(s):
            print('  ' + s.ljust(9) + str(l_status[s]))
    if l_status.get('ready'):
        print("Report only -- nothing changed. Run with --apply to rename the 'ready' items.")
    print('Wrote ' + l_out)


if __name__ == '__main__':
    main()
