"""
VMI Update Process - Utility Functions
Handles order validation, email notifications, and helper functions
"""

from smtplib import SMTP
from ssl import create_default_context
from os import path, rename, environ
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from log import l_log_location
from datetime import datetime


def check_order(p_dict, p_item_list):
    """
    Validate P21 order response and determine status.
    Returns: (status, order_no_or_reason, message)
      - 'success' : all items accepted
      - 'partial' : order created but some items were dropped (Delete=Y)
      - 'error'   : order was not created
    """
    if 'ResourceError' in p_dict:
        l_error_message = str(p_dict['ResourceError'].get('ErrorMessage', 'Unknown Error'))
        if 'This item ID is not valid' in l_error_message:
            l_err_message = 'One or more item IDs are not valid: ' + ','.join(p_item_list)
            return 'error', l_err_message, l_err_message
        else:
            l_full_error = 'Unknown Error: ' + l_error_message + ' Items: ' + ','.join(p_item_list)
            return 'error', l_full_error, l_full_error

    l_items = p_dict['Order']['Lines']['OrderLine']
    l_message = ""

    if type(l_items) is dict:
        if l_items['Delete'] == 'Y':
            l_message = 'OrderNo: ' + p_dict['Order']['OrderNo'] + '\nItemId: ' + l_items['ItemId'] + ' is not available to purchase\n'
    else:
        if any(d['Delete'] == 'Y' for d in l_items):
            l_message = 'OrderNo: ' + p_dict['Order']['OrderNo'] + '\n'
            for item in l_items:
                if item['Delete'] == 'Y':
                    l_message += 'ItemId: ' + item['ItemId'] + ' is not available to purchase\n'

    if l_message == "":
        return 'success', p_dict['Order']['OrderNo'], l_message
    else:
        return 'partial', p_dict['Order']['OrderNo'], l_message


def coalesce(p_value):
    """Handle None type/null for existing pricing in SQL Server"""
    if p_value is None:
        return 'null'
    return '{:.4f}'.format(p_value)


def get_contract():
    """Get P21 contract ID from environment variable, default to NONE"""
    try:
        return environ['P21_CONTRACT_ID']
    except KeyError:
        return 'NONE'


def rename_log():
    """Rename log file with timestamp to preserve history"""
    l_timestamp = datetime.now().strftime('%Y_%m_%d_%H%M%S')
    rename(l_log_location, l_log_location[:-4] + '_' + l_timestamp + '.log')


def email(p_subject, p_message="", p_log=True, p_attach=True):
    """
    Send email notification with optional log file attachment.

    Args:
        p_subject : email subject line
        p_message : email body (used when p_log=False)
        p_log     : if True, attach/embed the log file
        p_attach  : if True, attach log as file; if False, embed in body
    """
    l_recip = ['VMI@afi-tools.com']

    msg = MIMEMultipart()
    msg['Subject'] = p_subject
    msg['From'] = 'afireports@afi-tools.com'
    msg['To'] = ','.join(l_recip)

    if p_log:
        with open(l_log_location, 'r') as f:
            mess = f.read()
        if p_attach:
            part = MIMEApplication(mess, Name=path.basename(l_log_location))
            part['Content-Disposition'] = 'attachment; filename="%s"' % path.basename(l_log_location)
        else:
            part = MIMEText(mess)
        msg.attach(part)
    else:
        msg.attach(MIMEText(p_message or ''))

    context = create_default_context()
    s = SMTP('smtp.office365.com', 587)
    s.starttls(context=context)
    s.login(msg['From'], 'Sog36064')
    s.sendmail(msg['From'], l_recip, msg.as_string())
    s.quit()
