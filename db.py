"""
VMI Update Process - Database Configuration and Queries

Machine-specific settings are read from config.ini via config.py.
"""

from sys import exit
from pyodbc import drivers, connect, Error
from utils import email, rename_log
from api import get_customer_name
from log import log_info, log_warning, log_error, log_shutdown
from config import get_sql_server_name, get_sql_db_name, get_supplier_key
import health

SUPPLIER_KEY = get_supplier_key()


def controlled_exit(p_message):
    #Log fatal error, send email, and exit
    log_error(p_message)
    log_shutdown()
    health.record_event('db_error', p_message)
    email('Matrix Auto (DB Error) for ' + get_customer_name())
    rename_log()
    exit()


def connect_db():
    #Connect to local Matrix SQL Server database.
    #Tries all available SQL Server ODBC drivers until one succeeds.
    
    l_drivers = [x for x in drivers() if 'SQL Server' in x]
    l_conn = None

    for i in l_drivers:
        try:
            l_conn = connect(
                'Driver=' + i + ';'
                'Server=' + get_sql_server_name() + ';'
                'Database=' + get_sql_db_name() + ';'
                'Trusted_Connection=yes;',
                autocommit=True
            )
            break
        except Exception as e:
            log_warning(str(e) + ' - "' + i + '": Driver used to connect was unsuccessful, trying other available drivers...')

    if not l_conn:
        controlled_exit('FATAL: Could not establish connection to the database. Check ODBC driver and/or connection parameters.')

    return l_conn


def close_db_conn(p_conn):
    #Close database connection
    try:
        p_conn.close()
    except Exception as e:
        log_warning(str(e) + ' - Database connection unable to close.')


def get_items(l_cursor):
    #Fetch all active items linked to AFI supplier for price sync
    try:
        l_cursor.execute(
            'select m.item_key, item_code, item_price, supplier_price '
            'from dbo.ent_item_master m, dbo.ent_item_suppliers s '
            'where m.item_key = s.item_key '
            'and s.supplier_key = ' + str(SUPPLIER_KEY) + ' '
            'and s.bool_bitul = 0 '
            'and m.bool_bitul = 0'
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def get_item_codes(l_cursor):
    #Fetch active item codes linked to AFI supplier, for image sync
    try:
        l_cursor.execute(
            'select m.item_code '
            'from dbo.ent_item_master m, dbo.ent_item_suppliers s '
            'where m.item_key = s.item_key '
            'and s.supplier_key = ' + str(SUPPLIER_KEY) + ' '
            'and s.bool_bitul = 0 '
            'and m.bool_bitul = 0'
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def get_orders(l_cursor):
    #Fetch all pending orders not yet sent to ERP, excluding any currently
    #marked in-flight in erp_send_state (crash/duplicate-submission guard --
    #see mark_inflight/clear_inflight/get_stale_inflight below).
    #status_key = 1 (Opened) is an allowlist, not a denylist against
    #status_key = 4 (Closed) specifically -- a Closed-but-unsent PO must
    #never be picked up regardless of send_erp, and any other status value
    #this table might ever contain is excluded by default unless it's
    #explicitly, unambiguously Opened. Both this and send_erp = 0 are
    #required together; neither replaces the other.
    try:
        l_cursor.execute(
            'select distinct po_key, po_code '
            'from dbo.ent_po_headers '
            'where po_key in (select po_key from dbo.ent_po_details) '
            'and send_erp = 0 '
            'and supplier_key = ' + str(SUPPLIER_KEY) + ' '
            'and bool_bitul = 0 '
            'and status_key = 1 '
            'and po_key not in (select po_key from dbo.erp_send_state)'
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def get_order_items(l_cursor, l_key):
    #Fetch line items for a specific purchase order
    try:
        l_cursor.execute(
            'select po_key, po_line_no, item_code, item_description, qty, unit_price '
            'from ENT_PO_DETAILS '
            'join ENT_ITEM_MASTER on ENT_PO_DETAILS.item_key = ENT_ITEM_MASTER.ITEM_KEY '
            'where po_key = ' + str(l_key)
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def update_item(l_cursor, l_key, l_code, l_new_price, l_old_price):
    #Update item price in both ent_item_master and ent_item_suppliers
    try:
        l_cursor.execute(
            'update dbo.ent_item_master '
            'set item_price = ' + l_new_price +
            ' where item_key = ' + str(l_key)
        )
        log_info('updated ent_item_master: ' + str(l_code) + ' , ' + l_old_price + ' -> ' + l_new_price)

        l_cursor.execute(
            'update dbo.ent_item_suppliers '
            'set supplier_price = ' + l_new_price +
            ' where item_key = ' + str(l_key)
        )
        log_info('updated ent_item_suppliers: ' + str(l_code) + ' , ' + l_old_price + ' -> ' + l_new_price)
    except Error as e:
        controlled_exit('FATAL: ' + str(e))


def update_order(l_cursor, l_key):
    #Mark order as sent to ERP (send_erp = 1) to prevent resubmission
    try:
        l_cursor.execute(
            'update dbo.ent_po_headers '
            'set send_erp = 1 '
            'where po_key = ' + str(l_key)
        )
        log_info('updated ent_po_headers: po_key = ' + str(l_key) + ' - updated send_erp from 0 to 1')
    except Error as e:
        controlled_exit('FATAL: ' + str(e))


def mark_inflight(l_cursor, l_key):
    #Record that a PO submission to P21 is starting, before calling the API.
    #If the script crashes or loses connection mid-submission, this row stays
    #and blocks automatic resubmission next run (see get_orders) until a
    #human resolves it via get_stale_inflight.
    try:
        l_cursor.execute(
            "insert into dbo.erp_send_state (po_key, status, updated_at) values (?, 'inflight', GETDATE())",
            l_key
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))


def clear_inflight(l_cursor, l_key):
    #Remove the in-flight guard once a submission's outcome is definitely
    #known (success, partial, or an explicit 'error' response from P21).
    #Only leave a row behind when the outcome is genuinely unknown (an
    #exception during submission) -- see orders() in main.py.
    try:
        l_cursor.execute('delete from dbo.erp_send_state where po_key = ?', l_key)
    except Error as e:
        controlled_exit('FATAL: ' + str(e))


def get_stale_inflight(l_cursor):
    #Flag orders that have been marked in-flight for over an hour -- almost
    #certainly a crash or lost connection mid-submission that needs a human
    #to check P21 and manually resolve (clear the row, or set send_erp = 1
    #if it turns out the order did go through)
    try:
        l_cursor.execute(
            "select po_key from dbo.erp_send_state "
            "where status = 'inflight' "
            "and updated_at < DATEADD(hour, -1, GETDATE())"
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def record_open_order(l_cursor, l_key, l_code, l_order_no):
    #Start tracking a successfully-created P21 order so check_open_orders()
    #in main.py can periodically ask P21 whether it's since been received/
    #closed, and flag it if it's been open too long -- see erp_open_orders.sql
    try:
        l_cursor.execute(
            'insert into dbo.erp_open_orders (po_key, po_code, order_no, submitted_at) '
            'values (?, ?, ?, GETDATE())',
            (l_key, l_code, l_order_no)
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))


def get_open_orders(l_cursor):
    #Fetch every P21 order still being tracked as open (not yet confirmed
    #Completed/Cancelled/Deleted), with days_open computed in SQL Server
    #(GETDATE()) rather than Python so it's not sensitive to any clock
    #difference between this script's machine and the DB server -- see
    #check_open_orders() in main.py
    try:
        l_cursor.execute(
            'select po_key, po_code, order_no, '
            'datediff(day, submitted_at, GETDATE()) as days_open '
            'from dbo.erp_open_orders'
        )
    except Error as e:
        controlled_exit('FATAL: ' + str(e))
    return l_cursor.fetchall()


def clear_open_order(l_cursor, l_key):
    #Stop tracking an order once P21 reports it resolved (Completed/
    #Cancelled/Deleted) -- see check_open_orders() in main.py
    try:
        l_cursor.execute('delete from dbo.erp_open_orders where po_key = ?', l_key)
    except Error as e:
        controlled_exit('FATAL: ' + str(e))