# VMI Update Process — Deployment Guide

Full step-by-step guide for deploying this repo to a client machine. For a
condensed version and the machine-upgrade path, see [README.md](README.md).

---

## Prerequisites

- Windows machine with network access to:
  - the local Matrix SQL Server on this machine's network
  - the AFI P21 API
  - SendGrid (outbound HTTPS, for email notifications)
  - the VMI Health Dashboard endpoint (outbound HTTPS)
- Python 3.x installed and on `PATH`
- Git installed
- A SQL Server ODBC driver installed — `db.py`'s `connect_db()` tries every
  installed driver whose name contains `'SQL Server'` until one connects, so
  any reasonably recent driver works
- The Windows account the scheduled tasks will run as must have SQL access
  to the Matrix database — `db.py` connects with `Trusted_Connection=yes`
  (Windows/AD auth); there is no SQL-authentication path in this codebase

---

## 1. Clone the repo

```
git clone <repo-url> C:\update_process
cd C:\update_process
```

## 2. Install Python dependencies

```
pip install -r requirements.txt
```

## 3. Run the `erp_send_state` migration

Open [sql/erp_send_state.sql](sql/erp_send_state.sql) and run it against
**this machine's** Matrix database — the same database you'll enter into
`collect_config.py` in the next step.

Skip this only if this exact table already exists here **and** matches that
schema — check first rather than assuming; see the file's own header comment
about Sun Hydraulics and Spare Parts, which had a separately hand-built
version of this table applied outside this repo.

`main.py -a orders` will fail with an "invalid object name 'erp_send_state'"
SQL error until this step is done.

## 4. Configure the machine (`config.ini` + credentials)

Run the interactive collector once — it writes `config.ini` **and** stores
credentials in Windows Credential Manager in a single pass:

```
python collect_config.py
```

It will prompt for:

**Database**
| Field | config.ini key | Notes |
|---|---|---|
| SQL Server Name | `[database] sql_server_name` | Required |
| SQL Database Name | `[database] sql_db_name` | Required |
| Supplier Key | `[database] supplier_key` | The AFI supplier's key within this Matrix instance. Default `1` |

**P21**
| Field | config.ini key | Notes |
|---|---|---|
| P21 Customer ID | `[p21] p21_customer_id` | Required |
| P21 Ship To ID | `[p21] p21_ship_to_id` | Optional — only if this customer has multiple ship-to locations |
| P21 Contract ID | `[p21] p21_contract_id` | Optional |
| Location ID | `[p21] location_id` | Default `10` |
| PO Prefix | `[p21] po_prefix` | Optional |

**Email**
| Field | config.ini key | Notes |
|---|---|---|
| Email To | `[email] email_to` | Comma-separated. Default `VMI@afi-tools.com` |
| Email CC | `[email] email_cc` | Optional, comma-separated |

**Health Reporter**
| Field | config.ini key | Notes |
|---|---|---|
| Health Dashboard Client Name | `[health] client_name` | Must exactly match (case-sensitive) the name provisioned server-side — see below |
| Health Dashboard Endpoint URL | `[health] endpoint_url` | e.g. `https://<app-name>.azurewebsites.net/health/intake` |

**Credentials** (Windows Credential Manager only — never written to
`config.ini`, never committed):
| Credential | Notes |
|---|---|
| P21 API Base URL | |
| P21 API Username | |
| P21 API Password | |
| Health Reporter Shared Secret | Provisioned on the `VMI-Health-Dashboard` side via `provision_client_secret.py`, using the **same client name** entered above — it's shown once at provision time, so get it from whoever ran that before starting this step |
| SendGrid API Key | |

**Already have `config.ini` and just need to (re)store credentials?** Run
`setup_credentials.py` instead — it skips `config.ini` entirely and only
prompts for the 5 credential values above.

Verify anything stored in Credential Manager at any point with:

```
python collect_config.py --verify
```
or
```
python setup_credentials.py --verify
```

> **Note on environment variables:** `collect_config.py`'s `auto_detect()`
> will pre-fill some prompts (`SQL_SERVER_NAME`, `SQL_DB_NAME`,
> `P21_CUSTOMER_ID`, `P21_SHIP_TO_ID`, `SUPPLIER_KEY`) from OS environment
> variables if they happen to be set, purely as a convenience default you can
> accept or overwrite. They are **not** read anywhere else — `config.py`
> reads only from `config.ini` at runtime. Setting them beforehand is
> optional, not a required deployment step.

## 5. Test manually before scheduling anything

```
python main.py -l debug              # price sync, verbose logging
python main.py -a orders -l debug    # order submission, verbose logging
```

- Check `logs/app.log` for errors.
- Confirm the email notification actually arrives (via SendGrid).
- Manually run `python health_reporter.py`, then check
  `logs/health_reporter.log` and confirm this client shows up at the
  dashboard's `/health/dashboard` page.

## 6. Configure Task Scheduler

Four scheduled tasks total. All run the same Python interpreter used above,
with **Start in** set to `C:\update_process`:

| Task | Command | Frequency | Notes |
|---|---|---|---|
| VMI Price Sync | `python main.py` | *(match the interval already used on other client machines — not fixed anywhere in this repo; confirm against a reference machine)* | |
| VMI Auto Orders | `python main.py -a orders` | *(runs within a bounded window per day on existing machines — confirm the exact interval the same way)* | |
| VMI Health Reporter | `python health_reporter.py` | Every 15 minutes, indefinitely | **Own, independent** Task Scheduler entry — must keep running even if the two tasks above hang or crash. Run whether user is logged on or not: **Yes** |
| VMI Script Updates | `update_scripts.bat` | Monthly | Pulls the latest code via `git pull` |
Steps for this one: 
  1. Create Basic Task
  2. "VMI Monthly Script Update" or whatever title you want
  3. Trigger monthly, select all months, and on the 1st of every month
  4. Start a program
  5. Program/Script: C:\update_process\update_scripts.bat
  6. Add arguments: keep empty
  7. Start in: C:\update_process

## 7. Post-deployment checklist

- [ ] `erp_send_state` exists on this machine's database and matches `sql/erp_send_state.sql`
- [ ] `config.ini` fully filled in; `collect_config.py --verify` passes
- [ ] Manual run of both `main.py` actions succeeded and the email notification arrived
- [ ] Manual run of `health_reporter.py` succeeded and this client is visible on the dashboard
- [ ] All four Task Scheduler entries are created, pointed at `C:\update_process`, and `health_reporter.py`'s is set to run whether logged on or not
- [ ] `logs/` is being written to and rotated correctly (see `rename_log()` in `utils.py`)

---

## Upgrading an already-deployed machine

This guide covers a brand-new machine end to end. If you're bringing an
existing, already-running machine onto a newer version of this repo instead,
use **"Integrating This Repo Onto an Already-Deployed Customer Machine"** in
[README.md](README.md) — the order of operations matters there for a
different reason (avoiding breaking a machine that's already mid-schedule).
