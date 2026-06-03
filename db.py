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

SUPPLIER_KEY = get_supplier_key()


def controlled_exit(p_message):
    #Log fatal error, send email, and exit
    log_error(p_message)
    log_shutdown()
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


def get_orders(l_cursor):
    #Fetch all pending orders not yet sent to ERP
    try:
        l_cursor.execute(
            'select distinct po_key, po_code '
            'from dbo.ent_po_headers '
            'where po_key in (select po_key from dbo.ent_po_details) '
            'and send_erp = 0 '
            'and supplier_key = ' + str(SUPPLIER_KEY) + ' '
            'and bool_bitul = 0'
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