"""
VMI Update Process - Control Panel (GUI)

A Tkinter front end over the same scripts/config already used from the
command line -- collect_config.py's fields, credentials.py's keyring
entries, and main.py/matrix_image_save.py/health_reporter.py's existing
CLI invocations. It does not reimplement any of that logic: every "Run"
button shells out to the real script exactly as Task Scheduler does today,
and every config field reads/writes the same config.ini via the same
schema collect_config.py already produces.

Safe to run on an already-configured, already-scheduled machine:
- Loading this tool never blanks out an existing config.ini value or a
  stored credential -- fields are pre-filled from what's already there,
  and a credential is only overwritten if you explicitly unlock and
  retype it.
- The Scheduled Tasks tab only ever creates a task it has first confirmed
  is genuinely absent (by name AND by the command it actually runs) -- it
  will not create a second entry for something already scheduled under a
  different name. See task_scheduler.py for why that check exists.

Usage:
    python control_panel.py
    pythonw control_panel.py     # no console window
"""

import os
import re
import sys
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from configparser import ConfigParser

import keyring

import collect_config
import credentials as _credentials
import task_scheduler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = collect_config.CONFIG_PATH
SERVICE_NAME = _credentials.SERVICE_NAME

DEFAULT_IMAGE_FOLDER = r'C:\Program Files (x86)\MATRIX-TM\Images\ItemPictures'

LOGO_PATH = os.path.join(SCRIPT_DIR, 'assets', 'afi_logo.png')
ICON_PATH = os.path.join(SCRIPT_DIR, 'assets', 'afi_icon.png')

# (field id, section, key, label, default)
CONFIG_FIELDS = {
    'sql_server_name':           ('database', 'sql_server_name', ''),
    'sql_db_name':                ('database', 'sql_db_name', ''),
    'supplier_key':                ('database', 'supplier_key', '1'),
    'p21_customer_id':            ('p21', 'p21_customer_id', ''),
    'p21_ship_to_id':              ('p21', 'p21_ship_to_id', ''),
    'p21_contract_id':            ('p21', 'p21_contract_id', ''),
    'location_id':                  ('p21', 'location_id', '10'),
    'po_prefix':                    ('p21', 'po_prefix', ''),
    'email_to':                      ('email', 'email_to', 'VMI@afi-tools.com'),
    'email_cc':                      ('email', 'email_cc', ''),
    'email_sales_cc':              ('email', 'email_sales_cc', ''),
    'images_base_url':            ('images', 'base_url', ''),
    'images_local_folder':      ('images', 'local_folder', DEFAULT_IMAGE_FOLDER),
    'health_client_name':        ('health', 'client_name', ''),
    'health_endpoint_url':      ('health', 'endpoint_url', ''),
    'health_catalog_endpoint_url': ('health', 'catalog_endpoint_url', ''),
}

# Mirrors collect_config.collect()'s own required-field validation exactly.
REQUIRED_FIELDS = [
    ('sql_server_name', 'SQL Server Name'),
    ('sql_db_name', 'SQL Database Name'),
    ('p21_customer_id', 'P21 Customer ID'),
    ('email_to', 'Email To'),
    ('health_client_name', 'Health Dashboard Client Name'),
    ('health_endpoint_url', 'Health Dashboard Endpoint URL'),
]
# Deliberately NOT required: Image Host Base URL. Unlike the other fields
# above, config.py's get_image_base_url() is only ever called (and only
# raises) when sync_images() actually runs -- README.md is explicit that a
# machine not running Item Image Sync needs no [images] config at all. The
# original collect_config.py CLI required it unconditionally anyway, which
# was harmless there (it only runs once at initial setup) but meant this
# Save button -- used repeatedly for small edits -- would silently refuse to
# write ANY change, including an unrelated one, on a machine that legitimately
# doesn't run Image Sync and so has this field blank.

# (keyring key, label)
CREDENTIAL_FIELDS = [
    ('P21_BASE_URL', 'P21 API Base URL'),
    ('P21_API_USERNAME', 'P21 API Username'),
    ('P21_API_PASSWORD', 'P21 API Password'),
    ('HEALTH_REPORTER_SECRET', 'Health Reporter Shared Secret'),
    ('SENDGRID_API_KEY', 'SendGrid API Key'),
]


def load_config_values():
    cp = ConfigParser()
    cp.read(CONFIG_PATH)

    values = {}
    for field_id, (section, key, default) in CONFIG_FIELDS.items():
        if cp.has_section(section):
            values[field_id] = cp.get(section, key, fallback=default)
        else:
            values[field_id] = default

    # Only fill blanks from auto-detect (env vars / db.py) -- never override
    # a value that's already in config.ini, existing or freshly typed.
    if not os.path.exists(CONFIG_PATH):
        detected = collect_config.auto_detect()
        if not values['sql_server_name']:
            values['sql_server_name'] = detected.get('SQL_SERVER_NAME', '')
        if not values['sql_db_name']:
            values['sql_db_name'] = detected.get('SQL_DB_NAME', '')
        if not values['p21_customer_id']:
            values['p21_customer_id'] = detected.get('P21_CUSTOMER_ID', '')
        if not values['p21_ship_to_id']:
            values['p21_ship_to_id'] = detected.get('P21_SHIP_TO_ID', '')
        if 'SUPPLIER_KEY' in detected:
            values['supplier_key'] = detected['SUPPLIER_KEY']

    return values


def save_config_values(values):
    cp = ConfigParser()
    cp['database'] = {
        'sql_server_name': values['sql_server_name'],
        'sql_db_name': values['sql_db_name'],
        'supplier_key': values['supplier_key'],
    }
    cp['p21'] = {
        'p21_customer_id': values['p21_customer_id'],
        'p21_ship_to_id': values['p21_ship_to_id'],
        'p21_contract_id': values['p21_contract_id'],
        'location_id': values['location_id'],
        'po_prefix': values['po_prefix'],
    }
    cp['email'] = {
        'email_to': values['email_to'],
        'email_cc': values['email_cc'],
        'email_sales_cc': values['email_sales_cc'],
    }
    cp['images'] = {
        'base_url': values['images_base_url'],
        'local_folder': values['images_local_folder'],
    }
    cp['health'] = {
        'client_name': values['health_client_name'],
        'endpoint_url': values['health_endpoint_url'],
        'catalog_endpoint_url': values['health_catalog_endpoint_url'],
    }
    with open(CONFIG_PATH, 'w') as f:
        cp.write(f)


class ScrollableFrame(ttk.Frame):
    """Plain vertical-scroll container so the Configure tab still fits on
    a small screen."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)

        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        # Mouse-wheel scrolling is wired up at the application level (see
        # ControlPanelApp) rather than here -- binding <Enter>/<Leave> on
        # just this canvas doesn't work: `body` is embedded as a real child
        # window covering nearly the entire canvas, so the canvas's own
        # surface is almost never actually under the cursor and those
        # events rarely fire.

    def scroll_units(self, units):
        self.canvas.yview_scroll(units, 'units')


class CredentialRow(ttk.Frame):
    """One credential field: shows stored/not-set status without ever
    revealing the stored value, and only becomes editable (and thus only
    ever gets written) after an explicit 'Change' click."""

    def __init__(self, parent, key, label):
        super().__init__(parent)
        self.key = key
        self.touched = False

        ttk.Label(self, text=label, width=34, anchor='w').grid(row=0, column=0, sticky='w')

        self.entry = ttk.Entry(self, width=36, show='*', state='disabled')
        self.entry.grid(row=0, column=1, padx=4)

        stored = bool(keyring.get_password(SERVICE_NAME, key))
        self.status_var = tk.StringVar(value='\u2713 stored' if stored else 'not set')
        self.status_label = ttk.Label(self, textvariable=self.status_var,
                                        foreground='green' if stored else 'gray')
        self.status_label.grid(row=0, column=2, padx=6)

        self.change_btn = ttk.Button(self, text='Change', command=self._on_change)
        self.change_btn.grid(row=0, column=3, padx=4)

    def _on_change(self):
        self.entry.config(state='normal')
        self.entry.delete(0, 'end')
        self.entry.focus_set()
        self.touched = True
        self.status_var.set('editing...')
        self.status_label.config(foreground='blue')

    def value_to_save(self):
        """Returns the new value if this field was unlocked and non-empty,
        else None -- meaning "leave whatever's already stored alone"."""
        if self.touched:
            val = self.entry.get().strip()
            if val:
                return val
        return None


class ConfigureTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.entries = {}
        self.cred_rows = {}

        # Packed first, side='bottom', so Save can never end up scrolled out
        # of view below a tall form -- it was previously placed inside the
        # scrollable body itself and became unreachable (short of dragging a
        # mouse-wheel-less scrollbar) once enough sections pushed it below
        # the visible window height.
        footer = ttk.Frame(self)
        footer.pack(side='bottom', fill='x')
        ttk.Separator(footer).pack(fill='x')
        btn_frame = ttk.Frame(footer)
        btn_frame.pack(fill='x', padx=10, pady=10)
        ttk.Button(btn_frame, text='Save', command=self.on_save).pack(side='left')
        self.status_var = tk.StringVar(value='')
        ttk.Label(btn_frame, textvariable=self.status_var).pack(side='left', padx=10)

        self.scroll = ScrollableFrame(self)
        self.scroll.pack(side='top', fill='both', expand=True)
        body = self.scroll.body

        values = load_config_values()

        self._section(body, 'Database', [
            ('sql_server_name', 'SQL Server Name', values),
            ('sql_db_name', 'SQL Database Name', values),
            ('supplier_key', 'Supplier Key', values),
        ])
        self._section(body, 'P21', [
            ('p21_customer_id', 'P21 Customer ID', values),
            ('p21_ship_to_id', 'P21 Ship To ID (optional)', values),
            ('p21_contract_id', 'P21 Contract ID (optional)', values),
            ('location_id', 'Location ID', values),
            ('po_prefix', 'PO Prefix (optional)', values),
        ])
        self._section(body, 'Email', [
            ('email_to', 'Email To (comma-separated)', values),
            ('email_cc', 'Email CC (optional)', values),
            ('email_sales_cc', 'Sales Email CC -- orders only (optional)', values),
        ])
        self._section(body, 'Item Images (only if this machine runs Item Image Sync)', [
            ('images_base_url', 'Image Host Base URL', values),
            ('images_local_folder', 'Local Image Folder', values),
        ])
        self._section(body, 'Health Reporter', [
            ('health_client_name', 'Health Dashboard Client Name', values),
            ('health_endpoint_url', 'Health Dashboard Endpoint URL', values),
            ('health_catalog_endpoint_url', 'Catalog Sync Endpoint URL (optional)', values),
        ])

        cred_frame = ttk.LabelFrame(body, text='Credentials (Windows Credential Manager -- never written to config.ini)')
        cred_frame.pack(fill='x', padx=10, pady=10)
        for key, label in CREDENTIAL_FIELDS:
            row = CredentialRow(cred_frame, key, label)
            row.pack(fill='x', padx=8, pady=3)
            self.cred_rows[key] = row

    def _section(self, parent, title, fields):
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(fill='x', padx=10, pady=6)
        for field_id, label, values in fields:
            row = ttk.Frame(frame)
            row.pack(fill='x', padx=8, pady=3)
            ttk.Label(row, text=label, width=44, anchor='w').pack(side='left')
            entry = ttk.Entry(row, width=50)
            entry.insert(0, values.get(field_id, ''))
            entry.pack(side='left', padx=4)
            self.entries[field_id] = entry

    def on_save(self):
        values = {field_id: entry.get().strip() for field_id, entry in self.entries.items()}

        missing = [label for field_id, label in REQUIRED_FIELDS if not values.get(field_id)]
        if missing:
            messagebox.showerror('Missing required fields', 'These fields are required:\n\n' + '\n'.join(missing))
            return

        save_config_values(values)

        changed = []
        for key, row in self.cred_rows.items():
            new_val = row.value_to_save()
            if new_val is not None:
                keyring.set_password(SERVICE_NAME, key, new_val)
                changed.append(key)
                row.touched = False
                row.entry.delete(0, 'end')
                row.entry.config(state='disabled')
                row.status_var.set('\u2713 stored')
                row.status_label.config(foreground='green')

        msg = f'config.ini saved to {CONFIG_PATH}.'
        if changed:
            msg += f' Updated credentials: {", ".join(changed)}.'
        self.status_var.set(msg)


class ProcessRunner:
    """Streams one subprocess's combined stdout/stderr into a Text widget
    instead of a console window. Disables the given buttons while a run is
    in flight so a stray click can't launch a second overlapping run from
    the GUI itself."""

    def __init__(self, text_widget, buttons):
        self.text = text_widget
        self.buttons = buttons
        self.queue = queue.Queue()
        self.running = False

    def start(self, label, cmd):
        if self.running:
            return
        self.running = True
        for b in self.buttons:
            b.config(state='disabled')
        self._append(f'\n$ {label}\n')
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()
        self.text.after(100, self._poll)

    def _worker(self, cmd):
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            proc = subprocess.Popen(
                cmd, cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=creationflags,
            )
            for line in proc.stdout:
                self.queue.put(('line', line))
            proc.wait()
            self.queue.put(('done', proc.returncode))
        except Exception as exc:
            self.queue.put(('line', f'ERROR launching process: {exc}\n'))
            self.queue.put(('done', -1))

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == 'line':
                    self._append(payload)
                elif kind == 'done':
                    self._append(f'--- finished (exit code {payload}) ---\n')
                    self.running = False
                    for b in self.buttons:
                        b.config(state='normal')
                    return
        except queue.Empty:
            pass
        self.text.after(100, self._poll)

    def _append(self, text_):
        self.text.config(state='normal')
        self.text.insert('end', text_)
        self.text.see('end')
        self.text.config(state='disabled')


class RunTab(ttk.Frame):
    ACTIONS = [
        ('Price Sync', [sys.executable, 'main.py']),
        ('Auto Orders', [sys.executable, 'main.py', '-a', 'orders']),
        ('Auto Orders (Quotes)', [sys.executable, 'main.py', '-a', 'orders', '-q']),
        ('Item Image Sync', [sys.executable, 'matrix_image_save.py']),
        ('Catalog Sync', [sys.executable, 'main.py', '-a', 'catalog']),
        ('Health Reporter (single run)', [sys.executable, 'health_reporter.py']),
        ('Verify Config/Credentials', [sys.executable, 'collect_config.py', '--verify']),
        ('Pull Latest Updates', ['cmd', '/c', 'update_scripts.bat']),
    ]

    def __init__(self, parent):
        super().__init__(parent)

        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill='x', padx=10, pady=10)

        output = tk.Text(self, height=24, state='disabled', wrap='word')
        output.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        buttons = []
        runner = ProcessRunner(output, buttons)

        for i, (label, cmd) in enumerate(self.ACTIONS):
            btn = ttk.Button(btn_bar, text=label,
                              command=lambda l=label, c=cmd: runner.start(l, c))
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky='ew')
            buttons.append(btn)

        clear_btn = ttk.Button(btn_bar, text='Clear Stale PO...', command=lambda: self._clear_stale(runner))
        clear_btn.grid(row=len(self.ACTIONS) // 4, column=len(self.ACTIONS) % 4, padx=4, pady=4, sticky='ew')
        buttons.append(clear_btn)

        for col in range(4):
            btn_bar.columnconfigure(col, weight=1)

    def _clear_stale(self, runner):
        po_code = simpledialog.askstring('Clear Stale PO', 'PO code to clear from erp_send_state tracking:', parent=self)
        if po_code and po_code.strip():
            runner.start(f'Clear Stale PO {po_code.strip()}',
                         [sys.executable, 'main.py', '-a', 'clear_stale', '--po-code', po_code.strip()])


class ScheduledTasksTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        top = ttk.Frame(self)
        top.pack(fill='x', padx=10, pady=10)
        ttk.Button(top, text='Refresh', command=self.refresh).pack(side='left')
        self.refreshing_var = tk.StringVar(value='')
        ttk.Label(top, textvariable=self.refreshing_var).pack(side='left', padx=10)

        self.rows_frame = ttk.Frame(self)
        self.rows_frame.pack(fill='both', expand=True, padx=10, pady=10)

        self.row_widgets = {}
        for spec in task_scheduler.CANONICAL_TASKS:
            self._build_row(spec)

        self.after(200, self.refresh)

    def _build_row(self, spec):
        row = ttk.Frame(self.rows_frame)
        row.pack(fill='x', pady=4)
        ttk.Label(row, text=spec['name'], width=24, anchor='w').pack(side='left')
        create_btn = ttk.Button(row, text='Create...', state='disabled',
                                  command=lambda k=spec['key']: self.on_create(k))
        # Packed before the status label and anchored right, so an arbitrarily
        # long detected task name (real data from this machine's own Task
        # Scheduler, not something we can size for in advance) can't push the
        # button off the row or get clipped fighting it for space -- ttk.Label
        # treats a fixed `width` as a hard character clip with no ellipsis,
        # which is exactly what was cutting long names off.
        create_btn.pack(side='right', padx=6)
        status_var = tk.StringVar(value='checking...')
        status_label = ttk.Label(row, textvariable=status_var, anchor='w')
        status_label.pack(side='left', padx=6, fill='x', expand=True)
        self.row_widgets[spec['key']] = (status_var, status_label, create_btn)

    def refresh(self):
        self.refreshing_var.set('Refreshing...')
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        status = task_scheduler.detect_status()
        self.after(0, lambda: self._apply_status(status))

    def _apply_status(self, status):
        self.refreshing_var.set('')
        for key, (status_var, status_label, create_btn) in self.row_widgets.items():
            state, name = status.get(key, ('missing', None))
            if state == 'found':
                status_var.set(f'Found ("{name}")')
                status_label.config(foreground='green')
                create_btn.config(state='disabled')
            elif state == 'found_other':
                status_var.set(f'Found under a different name: "{name}"')
                status_label.config(foreground='blue')
                create_btn.config(state='disabled')
            elif state == 'name_conflict':
                status_var.set(f'A task named "{name}" exists but runs something else -- check manually')
                status_label.config(foreground='orange')
                create_btn.config(state='disabled')
            else:
                status_var.set('Not found')
                status_label.config(foreground='red')
                create_btn.config(state='normal')

    def on_create(self, key):
        spec = next(s for s in task_scheduler.CANONICAL_TASKS if s['key'] == key)
        values = self._prompt_schedule(key)
        if values is None:
            return

        if key in ('price_sync', 'orders'):
            minutes = values.get('minutes', '')
            if not minutes.isdigit() or int(minutes) <= 0:
                messagebox.showerror('Invalid interval', 'Enter a whole number of minutes.')
                return
            schedule_args = task_scheduler.schedule_args_minute(int(minutes))
        elif key in ('images', 'catalog'):
            t = values.get('time', '')
            if not re.match(r'^\d{2}:\d{2}$', t):
                messagebox.showerror('Invalid time', 'Enter time as HH:MM.')
                return
            schedule_args = task_scheduler.schedule_args_daily(t)
        elif key == 'script_updates':
            t = values.get('time', '')
            if not re.match(r'^\d{2}:\d{2}$', t):
                messagebox.showerror('Invalid time', 'Enter time as HH:MM.')
                return
            schedule_args = task_scheduler.schedule_args_monthly_on_day(1, t)
        else:  # health
            schedule_args = task_scheduler.schedule_args_minute(15)

        run_as_user = values.get('run_as_user') or None
        run_as_password = values.get('run_as_password') or None
        if key == 'health' and not run_as_user:
            if not messagebox.askyesno(
                'Continue without a run-as account?',
                "Without a run-as account, Health Reporter will only run while someone is\n"
                "logged on to this machine -- it won't survive a reboot with nobody logged\n"
                "in, which defeats the point of it running independently of the other\n"
                "tasks (see DEPLOYMENT.md step 6). Continue anyway?"
            ):
                return

        program = sys.executable if spec['program_is_python'] else os.path.join(SCRIPT_DIR, 'update_scripts.bat')
        ok, output = task_scheduler.create_task(
            name=spec['name'], program=program, arguments=spec['arguments'],
            start_in=SCRIPT_DIR, schedule_args=schedule_args,
            run_as_user=run_as_user, run_as_password=run_as_password,
        )
        if ok:
            messagebox.showinfo('Task created', f'{spec["name"]} created successfully.')
        else:
            messagebox.showerror('Task creation failed', output or 'Unknown error from schtasks.')
        self.refresh()

    def _prompt_schedule(self, key):
        win = tk.Toplevel(self)
        win.title('Create Scheduled Task')
        win.grab_set()
        result = {}
        entries = {}
        row = 0

        if key in ('price_sync', 'orders'):
            ttk.Label(win, text='Run every N minutes.\nConfirm the right interval against a reference client\n'
                                  'machine first -- DEPLOYMENT.md notes this isn\'t fixed anywhere.',
                       justify='left').grid(row=row, column=0, columnspan=2, sticky='w', padx=10, pady=(10, 4))
            row += 1
            entries['minutes'] = ttk.Entry(win)
            entries['minutes'].grid(row=row, column=0, padx=10, pady=4, sticky='w')
            row += 1
        elif key in ('images', 'catalog'):
            ttk.Label(win, text='Time of day to run (HH:MM, 24-hour):').grid(
                row=row, column=0, sticky='w', padx=10, pady=(10, 4))
            row += 1
            entries['time'] = ttk.Entry(win)
            entries['time'].insert(0, '02:00')
            entries['time'].grid(row=row, column=0, padx=10, pady=4, sticky='w')
            row += 1
        elif key == 'script_updates':
            ttk.Label(win, text='Time of day to run on the 1st of each month (HH:MM):').grid(
                row=row, column=0, sticky='w', padx=10, pady=(10, 4))
            row += 1
            entries['time'] = ttk.Entry(win)
            entries['time'].insert(0, '03:00')
            entries['time'].grid(row=row, column=0, padx=10, pady=4, sticky='w')
            row += 1
        elif key == 'health':
            ttk.Label(win, text='Runs every 15 minutes automatically.', justify='left').grid(
                row=row, column=0, columnspan=2, sticky='w', padx=10, pady=(10, 2))
            row += 1
            ttk.Label(win, text="To make it run whether logged on or not (recommended -- see\n"
                                  "DEPLOYMENT.md), enter the Windows account the other VMI tasks run\n"
                                  "as. Leave blank to skip for now and set it later in Task Scheduler.",
                       justify='left').grid(row=row, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 6))
            row += 1
            ttk.Label(win, text='Run-as username (DOMAIN\\user):').grid(row=row, column=0, sticky='w', padx=10)
            entries['run_as_user'] = ttk.Entry(win, width=28)
            entries['run_as_user'].grid(row=row, column=1, padx=10, pady=2)
            row += 1
            ttk.Label(win, text='Password:').grid(row=row, column=0, sticky='w', padx=10)
            entries['run_as_password'] = ttk.Entry(win, width=28, show='*')
            entries['run_as_password'].grid(row=row, column=1, padx=10, pady=2)
            row += 1

        def on_ok():
            result['ok'] = True
            for k, e in entries.items():
                result[k] = e.get().strip()
            win.destroy()

        def on_cancel():
            result['ok'] = False
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=row, column=0, columnspan=2, pady=12)
        ttk.Button(btns, text='Create', command=on_ok).pack(side='left', padx=4)
        ttk.Button(btns, text='Cancel', command=on_cancel).pack(side='left', padx=4)

        win.wait_window()
        return result if result.get('ok') else None


class ControlPanelApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('VMI Control Panel')
        self.geometry('820x620')

        # Logged as failures are swallowed, not raised: a missing/corrupt
        # assets/ folder (e.g. someone's local copy predates this change)
        # should never stop the app from opening, just skip the branding.
        try:
            self._icon_photo = tk.PhotoImage(file=ICON_PATH)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

        try:
            header = ttk.Frame(self)
            header.pack(fill='x', side='top')
            self._logo_photo = tk.PhotoImage(file=LOGO_PATH)
            ttk.Label(header, image=self._logo_photo).pack(side='left', padx=10, pady=6)
            ttk.Separator(header, orient='horizontal').pack(side='bottom', fill='x')
        except Exception:
            pass

        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True)

        self.notebook = notebook
        self.configure_tab = ConfigureTab(notebook)
        notebook.add(self.configure_tab, text='Configure')
        notebook.add(RunTab(notebook), text='Run')
        notebook.add(ScheduledTasksTab(notebook), text='Scheduled Tasks')

        # Window-wide, not tied to hovering a specific widget -- bind_all is
        # the lowest-priority fallback in Tk's event dispatch, so it never
        # overrides a widget's own native scrolling (e.g. the Run tab's
        # Text log already scrolls itself); it only kicks in for widgets
        # that don't otherwise handle the wheel themselves, which covers
        # every plain Label/Entry/Frame in the Configure form.
        self.bind_all('<MouseWheel>', self._on_mousewheel)

    def _on_mousewheel(self, event):
        if str(self.notebook.select()) == str(self.configure_tab):
            self.configure_tab.scroll.scroll_units(int(-1 * (event.delta / 120)))


if __name__ == '__main__':
    # Double-clicking a .py (or running `python control_panel.py`) launches
    # the console-subsystem python.exe, which keeps a cmd window open behind
    # the GUI for the whole session. Relaunch once under the windowless
    # pythonw.exe instead and exit this process -- `--no-relaunch` stops the
    # loop if pythonw.exe isn't sitting next to this interpreter for some
    # reason. A brief console flash on launch is normal; it closes as soon
    # as the relaunch happens. A shortcut built against pythonw.exe directly
    # skips even that.
    if os.name == 'nt' and '--no-relaunch' not in sys.argv and sys.executable.lower().endswith('python.exe'):
        pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        if os.path.exists(pythonw):
            subprocess.Popen([pythonw, os.path.abspath(__file__), '--no-relaunch'], cwd=SCRIPT_DIR)
            sys.exit(0)

    app = ControlPanelApp()
    app.mainloop()
