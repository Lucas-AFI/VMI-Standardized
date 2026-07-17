"""
VMI Update Process - Machine Configuration Collector

Run this once on each machine during setup or migration.
Automatically pulls existing config from environment variables and the
current db.py (if present), prompts for anything missing, then writes
config.ini and stores API credentials in Windows Credential Manager.

Usage:
    python collect_config.py
"""

import os
import re
import getpass
import keyring
from configparser import ConfigParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.ini')
DB_PATH = os.path.join(SCRIPT_DIR, 'db.py')
SERVICE_NAME = 'VMI_AFI'


def auto_detect():
    #Pull whatever we can from existing env vars and db.py
    detected = {}

    for key in ['SQL_SERVER_NAME', 'SQL_DB_NAME', 'P21_CUSTOMER_ID', 'P21_SHIP_TO_ID', 'SUPPLIER_KEY']:
        val = os.environ.get(key, '')
        if val:
            detected[key] = val

    # Supplier key from hardcoded value in old db.py if not in env vars
    if 'SUPPLIER_KEY' not in detected and os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f:
                content = f.read()
            match = re.search(r'supplier_key\s*=\s*(\d+)', content)
            if match:
                detected['SUPPLIER_KEY'] = match.group(1)
                print(f'  [auto] SUPPLIER_KEY = {detected["SUPPLIER_KEY"]} (read from db.py)')
        except Exception:
            pass

    return detected


def prompt(label, default=None, secret=False):
    #Prompt user for a value, showing default if available
    if default:
        display = f'{label} [{default}]: '
    else:
        display = f'{label}: '

    if secret:
        value = getpass.getpass(display)
    else:
        value = input(display).strip()

    if not value and default:
        return default
    return value


def collect():
    print()
    print('VMI Update Process - Machine Configuration Setup')
    print('=' * 50)
    print('Detecting existing configuration...')
    print()

    detected = auto_detect()

    for key, val in detected.items():
        if key != 'SUPPLIER_KEY':
            print(f'  [auto] {key} = {val}')

    print()
    print('Enter values below. Press Enter to accept auto-detected values.')
    print()

    # Database
    print('--- Database ---')
    sql_server = prompt('SQL Server Name', detected.get('SQL_SERVER_NAME'))
    sql_db = prompt('SQL Database Name', detected.get('SQL_DB_NAME'))
    supplier_key = prompt('Supplier Key', detected.get('SUPPLIER_KEY', '1'))

    # P21
    print()
    print('--- P21 ---')
    customer_id = prompt('P21 Customer ID', detected.get('P21_CUSTOMER_ID'))
    ship_to_id = prompt('P21 Ship To ID (optional, press Enter to skip)', detected.get('P21_SHIP_TO_ID', ''))
    contract_id = prompt('P21 Contract ID (optional, press Enter to skip)', '')
    location_id = prompt('Location ID (default: 10)', '10')
    po_prefix = prompt('PO Prefix (optional, press Enter to skip)', '')

    # Email
    print()
    print('--- Email ---')
    email_to = prompt('Email To', 'VMI@afi-tools.com')
    email_cc = prompt('Email CC (optional, press Enter to skip)', '')

    # Health Reporter
    print()
    print('--- Health Reporter ---')
    health_client_name = prompt('Health Dashboard Client Name (e.g. "American Torch Tip")')
    health_endpoint_url = prompt('Health Dashboard Endpoint URL')

    # Validate required fields
    missing = []
    if not sql_server: missing.append('SQL Server Name')
    if not sql_db: missing.append('SQL Database Name')
    if not customer_id: missing.append('P21 Customer ID')
    if not email_to: missing.append('Email To')
    if not health_client_name: missing.append('Health Dashboard Client Name')
    if not health_endpoint_url: missing.append('Health Dashboard Endpoint URL')

    if missing:
        print()
        print('ERROR: The following required fields are missing:')
        for m in missing:
            print('  - ' + m)
        print('Please re-run collect_config.py.')
        return

    # Write config.ini
    config = ConfigParser()

    config['database'] = {
        'sql_server_name': sql_server,
        'sql_db_name': sql_db,
        'supplier_key': supplier_key,
    }

    config['p21'] = {
        'p21_customer_id': customer_id,
        'p21_ship_to_id': ship_to_id,
        'p21_contract_id': contract_id,
        'location_id': location_id,
        'po_prefix': po_prefix,
    }

    config['email'] = {
        'email_to': email_to,
        'email_cc': email_cc,
    }

    config['health'] = {
        'client_name': health_client_name,
        'endpoint_url': health_endpoint_url,
    }

    with open(CONFIG_PATH, 'w') as f:
        config.write(f)

    print()
    print(f'config.ini written to {CONFIG_PATH}')

    # Credentials
    print()
    print('--- API Credentials ---')
    print('These will be stored in Windows Credential Manager.')
    print()

    base_url = prompt('P21 API Base URL')
    api_username = prompt('P21 API Username')
    api_password = prompt('P21 API Password', secret=True)
    health_reporter_secret = prompt('Health Reporter Shared Secret', secret=True)
    sendgrid_api_key = prompt('SendGrid API Key', secret=True)

    keyring.set_password(SERVICE_NAME, 'P21_BASE_URL', base_url)
    keyring.set_password(SERVICE_NAME, 'P21_API_USERNAME', api_username)
    keyring.set_password(SERVICE_NAME, 'P21_API_PASSWORD', api_password)
    keyring.set_password(SERVICE_NAME, 'HEALTH_REPORTER_SECRET', health_reporter_secret)
    keyring.set_password(SERVICE_NAME, 'SENDGRID_API_KEY', sendgrid_api_key)

    print()
    print('API credentials stored in Windows Credential Manager.')

    # Summary
    print()
    print('=' * 50)
    print('Setup complete. Summary:')
    print(f'  SQL Server     : {sql_server}')
    print(f'  Database       : {sql_db}')
    print(f'  Supplier Key   : {supplier_key}')
    print(f'  Customer ID    : {customer_id}')
    print(f'  Ship To ID     : {ship_to_id or "(not set)"}')
    print(f'  Contract ID    : {contract_id or "(not set)"}')
    print(f'  Location ID    : {location_id}')
    print(f'  PO Prefix      : {po_prefix or "(not set)"}')
    print(f'  Email To       : {email_to}')
    print(f'  Email CC       : {email_cc or "(not set)"}')
    print(f'  Health Client  : {health_client_name}')
    print(f'  Health Endpoint: {health_endpoint_url}')
    print(f'  API/SMTP Creds : stored in Credential Manager')
    print()
    print('You can verify credentials anytime with: python collect_config.py --verify')
    print('You can edit config.ini directly at: ' + CONFIG_PATH)


def verify():
    print()
    print('Verifying stored credentials...')
    base_url = keyring.get_password(SERVICE_NAME, 'P21_BASE_URL')
    username = keyring.get_password(SERVICE_NAME, 'P21_API_USERNAME')
    password = keyring.get_password(SERVICE_NAME, 'P21_API_PASSWORD')
    health_secret = keyring.get_password(SERVICE_NAME, 'HEALTH_REPORTER_SECRET')
    sendgrid_key = keyring.get_password(SERVICE_NAME, 'SENDGRID_API_KEY')

    if base_url and username and password and health_secret and sendgrid_key:
        print('P21_BASE_URL           : ' + base_url)
        print('P21_API_USERNAME       : ' + username)
        print('P21_API_PASSWORD       : ' + '*' * len(password))
        print('HEALTH_REPORTER_SECRET : ' + '*' * len(health_secret))
        print('SENDGRID_API_KEY       : ' + '*' * len(sendgrid_key))
        print()
        print('All credentials found.')
    else:
        missing = []
        if not base_url: missing.append('P21_BASE_URL')
        if not username: missing.append('P21_API_USERNAME')
        if not password: missing.append('P21_API_PASSWORD')
        if not health_secret: missing.append('HEALTH_REPORTER_SECRET')
        if not sendgrid_key: missing.append('SENDGRID_API_KEY')
        print('Missing credentials: ' + ', '.join(missing))
        print('Please re-run collect_config.py to store them.')


if __name__ == '__main__':
    import sys
    if '--verify' in sys.argv:
        verify()
    else:
        collect()