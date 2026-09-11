"""
VMI Update Process - Task Scheduler helper

Thin wrapper around schtasks.exe used by control_panel.py's "Scheduled
Tasks" tab. Not imported by any of the actual scheduled scripts
(main.py/health_reporter.py/etc.) -- this only ever runs interactively,
from the GUI, when a human is looking at the result.

Detection deliberately checks more than just the canonical task name: this
repo's own DEPLOYMENT.md notes that task naming and frequency were never
fixed here, so client machines may already have an equivalent task under a
different name. Matching on the actual command (script + relevant flags)
instead avoids ever creating a second, duplicate task that runs the same
action twice -- see README.md's "Order Eligibility" section for why a
duplicate Auto Orders run in particular is a real, previously-realized
danger here, not a theoretical one.
"""

import csv
import io
import os
import re
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_schtasks(args):
    result = subprocess.run(
        ['schtasks'] + args,
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout, result.stderr


def list_tasks():
    """Return {task_name: record_dict} for every task currently registered.

    schtasks /query /fo csv /v repeats the CSV header once per task on some
    Windows versions, so header rows are detected and skipped rather than
    assumed to appear exactly once.
    """
    rc, out, _err = _run_schtasks(['/query', '/fo', 'csv', '/v'])
    if rc != 0:
        return {}

    tasks = {}
    header = None
    for row in csv.reader(io.StringIO(out)):
        if not row:
            continue
        if row[0] == 'HostName':
            header = row
            continue
        if header is None:
            continue
        record = dict(zip(header, row))
        name = (record.get('TaskName') or '').lstrip('\\')
        if name:
            tasks[name] = record
    return tasks


def _normalized_command(record):
    task_to_run = (record.get('Task To Run') or '').lower()
    start_in = (record.get('Start In') or '').strip('"').lower()
    return task_to_run, start_in


def _in_this_folder(start_in):
    if not start_in:
        # Some schtasks builds leave "Start In" blank even when /tr embeds
        # an absolute path under SCRIPT_DIR -- treat blank as "unknown"
        # rather than "no match" so the task-to-run text is still checked.
        return True
    return os.path.normcase(os.path.normpath(start_in)) == os.path.normcase(os.path.normpath(SCRIPT_DIR))


# Each entry: key, canonical display name used when *creating* a new task,
# and a predicate over (task_to_run_lower, start_in_lower) deciding whether
# an existing task already covers this action, regardless of its name.
CANONICAL_TASKS = [
    {
        'key': 'price_sync',
        'name': 'VMI Price Sync',
        'match': lambda cmd, start_in: 'main.py' in cmd
            and not re.search(r'-a\s+(orders|images|catalog|clear_stale)', cmd)
            and _in_this_folder(start_in),
        'program_is_python': True,
        'arguments': 'main.py',
    },
    {
        'key': 'orders',
        'name': 'VMI Auto Orders',
        'match': lambda cmd, start_in: 'main.py' in cmd
            and re.search(r'-a\s+orders', cmd)
            and _in_this_folder(start_in),
        'program_is_python': True,
        'arguments': 'main.py -a orders',
    },
    {
        'key': 'images',
        'name': 'VMI Item Image Sync',
        'match': lambda cmd, start_in: (
            'matrix_image_save.py' in cmd
            or ('main.py' in cmd and re.search(r'-a\s+images', cmd))
        ) and _in_this_folder(start_in),
        'program_is_python': True,
        'arguments': 'matrix_image_save.py',
    },
    {
        'key': 'catalog',
        'name': 'VMI Catalog Sync',
        'match': lambda cmd, start_in: 'main.py' in cmd
            and re.search(r'-a\s+catalog', cmd)
            and _in_this_folder(start_in),
        'program_is_python': True,
        'arguments': 'main.py -a catalog',
    },
    {
        'key': 'health',
        'name': 'VMI Health Reporter',
        'match': lambda cmd, start_in: 'health_reporter.py' in cmd
            and _in_this_folder(start_in),
        'program_is_python': True,
        'arguments': 'health_reporter.py',
    },
    {
        'key': 'script_updates',
        'name': 'VMI Script Updates',
        'match': lambda cmd, start_in: 'update_scripts.bat' in cmd
            and _in_this_folder(start_in),
        'program_is_python': False,
        'arguments': '',
    },
]


def detect_status():
    """For each canonical task, report ('found', name) / ('found_other', existing_name) / ('missing', None)."""
    tasks = list_tasks()
    status = {}
    for spec in CANONICAL_TASKS:
        key = spec['key']
        canonical_name = spec['name']
        if canonical_name in tasks:
            cmd, start_in = _normalized_command(tasks[canonical_name])
            if spec['match'](cmd, start_in):
                status[key] = ('found', canonical_name)
                continue
            # A task exists under the canonical name but runs something
            # else -- never silently adopt or overwrite it.
            status[key] = ('name_conflict', canonical_name)
            continue

        found_other = None
        for name, record in tasks.items():
            cmd, start_in = _normalized_command(record)
            if spec['match'](cmd, start_in):
                found_other = name
                break

        if found_other:
            status[key] = ('found_other', found_other)
        else:
            status[key] = ('missing', None)

    return status


def create_task(name, program, arguments, start_in, schedule_args, run_as_user=None, run_as_password=None):
    """Create one scheduled task. Never passes /f -- if a task with this
    exact name already exists, schtasks fails loudly instead of silently
    overwriting it.

    schtasks.exe /create has no direct "start in" flag, so the working
    directory is set the standard way: wrapping the real command in
    `cmd /c cd /d "<dir>" && ...`. This matters less than it would elsewhere
    in this repo -- config.py/log.py/collect_config.py all resolve their
    paths relative to their own __file__, not the process cwd -- but it
    keeps newly-created tasks consistent with what DEPLOYMENT.md documents
    ("Start in: C:\\update_process") for anyone inspecting Task Scheduler by
    hand later.
    """
    inner = f'"{program}" {arguments}'.strip()
    tr = f'cmd /c cd /d "{start_in}" && {inner}'
    args = [
        '/create',
        '/tn', name,
        '/tr', tr,
        '/rl', 'LIMITED',
    ] + schedule_args

    if run_as_user:
        args += ['/ru', run_as_user]
        if run_as_password:
            args += ['/rp', run_as_password]
    else:
        # No /ru -- runs only while this user is logged on. Fine for
        # everything except Health Reporter, which is documented as needing
        # to run whether logged on or not (see DEPLOYMENT.md step 6).
        pass

    rc, out, err = _run_schtasks(args)
    return rc == 0, (out or '') + (err or '')


def schedule_args_minute(every_n_minutes):
    return ['/sc', 'MINUTE', '/mo', str(every_n_minutes)]


def schedule_args_daily(start_time_hhmm, every_n_days=1):
    return ['/sc', 'DAILY', '/mo', str(every_n_days), '/st', start_time_hhmm]


def schedule_args_monthly_on_day(day_of_month, start_time_hhmm):
    return ['/sc', 'MONTHLY', '/d', str(day_of_month), '/st', start_time_hhmm]
