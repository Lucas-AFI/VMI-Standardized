"""
VMI Update Process - Item Image Sync

Downloads item images from the configured image host into the local MATRIX
image folder, matching each item_code to <item_code>.jpg. Machine-specific
settings (base_url, local_folder) come from config.ini via config.py, same
as every other module here. The image host is assumed to be a public,
unauthenticated endpoint (matching the original script this was
standardized from) -- no credentials are involved.

sync_images() is the full orchestration (matches items()/orders() in
main.py: logging, health reporting, log rotation). It's called from two
places on purpose:
  - main.py's `-a images` action, for consistency with the other actions
  - matrix_image_save.py, a thin no-argument entry point kept specifically
    so existing Task Scheduler entries across client machines (Program:
    python, Arguments: matrix_image_save.py) don't need to change
Both call the exact same function -- there is no behavior difference
between the two invocations.
"""

import os
import traceback
import requests
from config import get_image_base_url, get_local_image_folder
from db import connect_db, close_db_conn, get_item_codes
from log import log_debug, log_error, start_log, stop_log
from utils import rename_log
import health


def ensure_image_folder():
    os.makedirs(get_local_image_folder(), exist_ok=True)


def download_item_image(p_item_code):
    """
    Download a single item's image to the local MATRIX image folder.
    Returns:
      'ok'      - downloaded and written successfully
      'missing' - host returned 404 (no image for this item -- routine, not
                  an error; not every item necessarily has a photo yet)
      'error'   - network failure, an unexpected HTTP response, or a local
                  file-write failure (e.g. permissions) -- logs the specific
                  reason itself, since a single item's failure must not take
                  down the whole run (see sync_images() below).
    """
    l_image_url = get_image_base_url() + '/' + p_item_code + '.jpg'
    l_target_path = os.path.join(get_local_image_folder(), p_item_code + '.jpg')

    try:
        l_response = requests.get(l_image_url, timeout=10)
    except Exception as e:
        log_error('Image download failed for ' + p_item_code + ': ' + str(e))
        return 'error'

    if l_response.status_code == 404:
        return 'missing'
    elif l_response.status_code != 200:
        log_error('Image host returned HTTP ' + str(l_response.status_code) + ' for ' + p_item_code)
        return 'error'

    try:
        with open(l_target_path, 'wb') as f:
            f.write(l_response.content)
    except OSError as e:
        log_error('Failed to write image for ' + p_item_code + ' to ' + l_target_path + ': ' + str(e))
        return 'error'

    return 'ok'


def sync_images():
    # Download item images from the image host into the local MATRIX folder
    l_tot_cnt = 0
    l_succ_cnt = 0
    l_err_cnt = 0

    start_log('Image sync process')

    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()
        l_rows = get_item_codes(l_cursor)
        close_db_conn(l_db_conn)

        ensure_image_folder()

        for row in l_rows:
            l_tot_cnt += 1
            l_result = download_item_image(row.item_code)

            if l_result == 'ok':
                log_debug('Downloaded image: ' + row.item_code + '.jpg')
                l_succ_cnt += 1
            elif l_result == 'missing':
                log_debug('No image found for: ' + row.item_code)
            else:
                # download_item_image() already logged the specific reason
                l_err_cnt += 1

        stop_log('Image sync process', l_succ_cnt, l_tot_cnt)

        health.record_run('images', 'success', l_succ_cnt, l_tot_cnt, l_err_cnt)

        rename_log()
    except Exception:
        health.record_event('run_failure', traceback.format_exc()[:2000])
        health.record_run('images', 'error', l_succ_cnt, l_tot_cnt, l_err_cnt)
        raise
