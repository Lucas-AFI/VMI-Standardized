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

Report: fix_item_ids_report.csv. Status values:
    ready      -- one clear replacement, safe to rename
    updated    -- renamed (--apply)
    conflict   -- new ID is already a Matrix item code, two old codes map to
                  it, or it's too long for Matrix; a person decides
    no match   -- P21 has no replacement on file; manual lookup
    error      -- lookup or update failed; rerun

Only ENT_ITEM_MASTER.ITEM_CODE changes. Matrix links POs, stock, etc. by
item_key, so history stays attached. Every rename is logged in
dbo.vmi_item_code_changes (created on first --apply), which --undo reads.
"""

import argparse
import csv
from collections import Counter
from datetime import datetime
from os import path

from db import connect_db, close_db_conn, get_items
from item_id_lookup import odata, lookup
from price_lookup import p21_token
from utils import build_xlsx

BATCH = 40   # item IDs per P21 existence check (keeps the URL short)
LOG_TABLE = 'dbo.vmi_item_code_changes'
HEADER = ['Item Key', 'Old Item #', 'New Item #', 'New Item Description', 'Status', 'Note']


def _progress(p_on, p_text):
    if p_on:
        print('\r' + p_text, end='', flush=True)


def p21_live_ids(p_headers, p_codes, p_progress=True):
    # Upper-cased set of the codes P21 has as live (not deleted) items.
    l_live = set()
    for i in range(0, len(p_codes), BATCH):
        l_batch = p_codes[i:i + BATCH]
        l_filter = ' or '.join("item_id eq '" + c.replace("'", "''") + "'" for c in l_batch)
        for l_item in odata(p_headers, 'p21_view_inv_mast', l_filter):
            if l_item.get('delete_flag') != 'Y':
                l_live.add(str(l_item['item_id']).strip().upper())
        _progress(p_progress, 'Checked ' + str(min(i + BATCH, len(p_codes))) + '/' + str(len(p_codes)) + ' items in P21')
    _progress(p_progress, '\n')
    return l_live


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
    l_live = p21_live_ids(l_headers, sorted(set(l_items.values())), p_progress)

    l_rows = []
    l_bad = [(k, c) for k, c in l_items.items() if c.upper() not in l_live]
    for n, (l_key, l_old) in enumerate(l_bad, 1):
        try:
            l_new, l_desc, l_note = lookup(l_headers, l_old)
            l_status = 'ready' if l_new else 'no match'
        except Exception as e:
            l_new, l_desc, l_note, l_status = '', '', str(e)[:200], 'error'
        l_rows.append([l_key, l_old, l_new, l_desc, l_status, l_note])
        _progress(p_progress, 'Looked up ' + str(n) + '/' + str(len(l_bad)) + ' unrecognized items')
    _progress(p_progress and l_bad, '\n')

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

    l_out = path.abspath('fix_item_ids_report.csv')
    with open(l_out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(l_rows)

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
