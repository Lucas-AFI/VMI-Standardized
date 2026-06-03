#VMI Update Process - Configuration

#Reads machine-specific settings from config.ini.
#All scripts import from this module instead of reading env vars directly.

#Required fields: sql_server_name, sql_db_name, p21_customer_id, email_to
#Optional fields: all others (safe defaults applied where possible)


from configparser import ConfigParser
from sys import exit
from os import path

_CONFIG_PATH = path.join(path.dirname(path.abspath(__file__)), 'config.ini')
_config = ConfigParser()

if not _config.read(_CONFIG_PATH):
    print('ERROR: config.ini not found at ' + _CONFIG_PATH)
    print('Please run collect_config.py to generate it.')
    exit(1)


def _get(section, key, fallback=None, required=False):
    value = _config.get(section, key, fallback=fallback)
    if required and not value:
        print(f'ERROR: Required config value [{section}] {key} is missing.')
        print('Please run collect_config.py to fix config.ini.')
        exit(1)
    return value or fallback


# Database

def get_sql_server_name():
    return _get('database', 'sql_server_name', required=True)

def get_sql_db_name():
    return _get('database', 'sql_db_name', required=True)

def get_supplier_key():
    return int(_get('database', 'supplier_key', fallback='1'))


# P21 enpoints

def get_customer_id():
    return _get('p21', 'p21_customer_id', required=True)

def get_ship_to_id():
    return _get('p21', 'p21_ship_to_id', fallback=None)

def get_contract_id():
    return _get('p21', 'p21_contract_id', fallback=None)

def get_location_id():
    return _get('p21', 'location_id', fallback='10')

def get_po_prefix():
    return _get('p21', 'po_prefix', fallback=None)


# For Email

def get_email_to():
    raw = _get('email', 'email_to', required=True)
    return [addr.strip() for addr in raw.split(',') if addr.strip()]

def get_email_cc():
    raw = _get('email', 'email_cc', fallback='')
    return [addr.strip() for addr in raw.split(',') if addr.strip()]