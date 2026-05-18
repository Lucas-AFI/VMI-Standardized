"""
VMI Update Process - Logging Configuration
"""

from pathlib import Path
from logging import basicConfig, getLogger, debug, info, warning, error, shutdown, DEBUG, INFO, WARNING, ERROR

l_log_location = 'logs/app.log'
l_log_level = INFO


def configure_logs():
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
