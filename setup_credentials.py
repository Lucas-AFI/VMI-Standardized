"""
VMI Update Process - Credential Setup (standalone)

Use this only if you need to update credentials without re-running
the full collect_config.py setup.

Usage:
    python setup_credentials.py
    python setup_credentials.py --verify
"""

import keyring
import getpass
import sys

SERVICE_NAME = 'VMI_AFI'


def setup():
    print()
    print('VMI Credential Setup')
    print('=' * 40)
    print('Credentials will be stored in Windows Credential Manager.')
    print('Run this as the same user account that the scheduled tasks run as.')
    print()

    base_url = input('Enter P21 API base URL: ').strip()
    username = input('Enter P21 API username: ').strip()
    password = getpass.getpass('Enter P21 API password: ')

    keyring.set_password(SERVICE_NAME, 'P21_BASE_URL', base_url)
    keyring.set_password(SERVICE_NAME, 'P21_API_USERNAME', username)
    keyring.set_password(SERVICE_NAME, 'P21_API_PASSWORD', password)

    print()
    print('Credentials stored successfully.')
    print('Verify anytime with: python setup_credentials.py --verify')


def verify():
    print()
    print('Verifying stored credentials...')
    base_url = keyring.get_password(SERVICE_NAME, 'P21_BASE_URL')
    username = keyring.get_password(SERVICE_NAME, 'P21_API_USERNAME')
    password = keyring.get_password(SERVICE_NAME, 'P21_API_PASSWORD')

    if base_url and username and password:
        print('P21_BASE_URL     : ' + base_url)
        print('P21_API_USERNAME : ' + username)
        print('P21_API_PASSWORD : ' + '*' * len(password))
        print()
        print('All credentials found.')
    else:
        missing = []
        if not base_url: missing.append('P21_BASE_URL')
        if not username: missing.append('P21_API_USERNAME')
        if not password: missing.append('P21_API_PASSWORD')
        print('Missing credentials: ' + ', '.join(missing))
        print('Please re-run collect_config.py to store them.')


if __name__ == '__main__':
    if '--verify' in sys.argv:
        verify()
    else:
        setup()