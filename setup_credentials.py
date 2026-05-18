"""
VMI Update Process - One-Time Credential Setup

Run this script once per machine during initial deployment.
Must be run as the same Windows user that the scheduled tasks run as.

Usage:
    python setup_credentials.py
"""

import keyring
import getpass

SERVICE_NAME = 'VMI_AFI'

def setup():
    print('VMI Credential Setup')
    print('=' * 40)
    print('These credentials will be stored securely in Windows Credential Manager.')
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
    print('You can verify by running: python setup_credentials.py --verify')

def verify():
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
        print('Please re-run setup_credentials.py to store them.')

if __name__ == '__main__':
    import sys
    if '--verify' in sys.argv:
        verify()
    else:
        setup()
