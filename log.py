"""
VMI Update Process - Logging Configuration
"""

from pathlib import Path
from logging import basicConfig, getLogger, debug, info, warning, error, shutdown, DEBUG, INFO, WARNING, ERROR

l_script_dir = Path(__file__).resolve().parent
l_log_dir = l_script_dir / 'logs'
l_log_location = str(l_log_dir / 'app.log')  # default; configure_logs() overwrites per-action
l_log_level = INFO


def configure_logs(p_action='app'):
    #Each action (items/orders/images) gets its own log file, not a shared
    #one -- Price Sync and Auto Orders are separate scheduled tasks that can
    #genuinely overlap in wall-clock time (a large catalog can take a while,
    #especially with the price-sync retry/pause logic), and two processes
    #sharing one file meant one could crash trying to rename a file the
    #other still had open (WinError 32), or silently clobber the other's
    #in-progress content. See utils.py's rename_log()/email(), which read
    #this value dynamically via the log module rather than a stale copy.
    global l_log_location
    l_log_location = str(l_log_dir / ('app_' + p_action + '.log'))
    path = Path(l_log_location)
    path.parent.mkdir(exist_ok=True)
    basicConfig(
        filename=l_log_location,
        filemode='w',
        format='%(asctime)s -  %(levelname)s: %(message)s',
        datefmt='%d-%b-%y %H:%M:%S',
        level=l_log_level
    )


def set_level(p_level):
    logger = getLogger()
    levels = {'DEBUG': DEBUG, 'INFO': INFO, 'WARN': WARNING, 'ERROR': ERROR}
    if p_level in levels:
        logger.setLevel(levels[p_level])


def start_log(p_type):
    debug('')
    debug('*********** ' + p_type + ' started ***********')
    debug('')


def stop_log(p_type, p_succ_cnt, p_tot_cnt):
    debug('')
    debug('SUMMARY: ' + str(p_succ_cnt) + ' of ' + str(p_tot_cnt) + ' records successfully updated')
    debug('')
    debug('*********** ' + p_type + ' completed ***********')
    debug('')
    shutdown()


def log_error(p_value):
    error(p_value)


def log_info(p_value):
    info(p_value)


def log_debug(p_value):
    debug(p_value)


def log_warning(p_value):
    warning(p_value)


def log_shutdown():
    shutdown()
