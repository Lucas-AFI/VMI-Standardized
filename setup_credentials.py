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
    health_secret = getpass.getpass('Enter Health Reporter shared secret: ')
    sendgrid_key = getpass.getpass('Enter SendGrid API key: ')

    keyring.set_password(SERVICE_NAME, 'P21_BASE_URL', base_url)
    keyring.set_password(SERVICE_NAME, 'P21_API_USERNAME', username)
    keyring.set_password(SERVICE_NAME, 'P21_API_PASSWORD', password)
    keyring.set_password(SERVICE_NAME, 'HEALTH_REPORTER_SECRET', health_secret)
    keyring.set_password(SERVICE_NAME, 'SENDGRID_API_KEY', sendgrid_key)

    print()
    print('Credentials stored successfully.')
    print('Verify anytime with: python setup_credentials.py --verify')


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
    if '--verify' in sys.argv:
        verify()
    else:
        setup()