"""
VMI Update Process - Credential Access

Retrieves API credentials from Windows Credential Manager at runtime.
Credentials are stored during initial setup via setup_credentials.py.
Never import or log the values returned by this module.
"""

import keyring
from sys import exit

SERVICE_NAME = 'VMI_AFI'


def _get(key):
    value = keyring.get_password(SERVICE_NAME, key)
    if not value:
        print(f'ERROR: Credential "{key}" not found in Windows Credential Manager.')
        print('Please run setup_credentials.py to store credentials for this machine.')
        exit(1)
    return value


def get_base_url():
    return _get('P21_BASE_URL')


def get_api_username():
    return _get('P21_API_USERNAME')


def get_api_password():
    return _get('P21_API_PASSWORD')


def get_health_reporter_secret():
    return _get('HEALTH_REPORTER_SECRET')


def get_sendgrid_api_key():
    return _get('SENDGRID_API_KEY')
