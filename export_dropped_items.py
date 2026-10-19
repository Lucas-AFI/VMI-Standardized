"""
VMI Update Process - Dropped Items Export

Exports partial_order events (items P21 dropped from a submitted order --
see check_order() in utils.py) from this machine's local health_state.db to
a CSV, one row per order (not per item -- the detail column already lists
every dropped item + probable cause for that order).

Usage:
    python export_dropped_items.py            # last 30 days
    python export_dropped_items.py --days 90
"""

import csv
import sys
from argparse import ArgumentParser
from datetime import datetime, timedelta, timezone

import health


def export(p_days, p_outfile):
    l_cutoff = (datetime.now(timezone.utc) - timedelta(days=p_days)).isoformat()
    l_conn = health._connect()
    l_rows = l_conn.execute(
        'select created_at, po_code, detail from events '
        'where event_type = ? and created_at >= ? '
        'order by created_at desc',
        ('partial_order', l_cutoff)
    ).fetchall()
    l_conn.close()

    with open(p_outfile, 'w', newline='', encoding='utf-8') as f:
        l_writer = csv.writer(f)
        l_writer.writerow(['created_at', 'po_code', 'detail'])
        l_writer.writerows(l_rows)

    print(f'{len(l_rows)} dropped-order event(s) in the last {p_days} day(s) -> {p_outfile}')


if __name__ == '__main__':
    parser = ArgumentParser(description='Export dropped-item events to CSV')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--out', default='dropped_items.csv')
    args = parser.parse_args()
    export(args.days, args.out)
