# VMI Update Process

Standardized scripts for AFI Matrix VMI vending cabinet client machines.
Each machine runs three scheduled tasks:

- **Price Sync** — syncs item prices from P21 to the local Matrix database
- **Auto Orders** — submits pending Matrix purchase orders to P21
- **Health Reporter** — reports machine/run health to the central dashboard every 15 minutes (runs independently, see below)

---

## Repository Structure

```
VMI-Standardized/
├── main.py                 # Entry point — price sync and order submission
├── api.py                  # AFI P21 API integration
├── db.py                   # Database connection and queries
├── xml_processor.py        # P21 order XML builder
├── utils.py                # Order validation, email notifications, helpers
├── log.py                  # Logging configuration
├── credentials.py          # Credential decryption (do not modify)
├── setup_credentials.py    # One-time credential setup per machine (run as admin)
├── health.py                # Local health event queue, written to by main.py/db.py
├── health_reporter.py       # Standalone script, own scheduled task, posts health to the central dashboard
├── sql/
│   └── erp_send_state.sql  # One-time per-machine migration — duplicate-order guard, see below
├── update_scripts.bat      # Monthly git pull scheduled task
├── requirements.txt        # Python dependencies
└── DEPLOYMENT.md           # Full deployment guide
```

> `credentials.enc` is generated locally by `setup_credentials.py` and is **never committed to this repo**.

---

## Quick Start (new machine deployment)

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full step-by-step guide. The short version:

1. Clone the repo to `C:\update_process`
2. Install dependencies: `pip install -r requirements.txt`
3. Set environment variables (see DEPLOYMENT.md)
4. Run [sql/erp_send_state.sql](sql/erp_send_state.sql) once against this machine's Matrix SQL
   Server database — required before `main.py -a orders` will run at all (see Duplicate-Order Guard
   below)
5. Run `setup_credentials.py` once to store API, SendGrid, and health reporter credentials securely
6. Fill in the `[health]` section of `config.ini` (`client_name`, `endpoint_url`)
7. Test manually, then configure scheduled tasks — including a **third, independent** Task Scheduler
   entry for `health_reporter.py` (every 15 minutes, runs whether user is logged on or not)

---

## Integrating This Repo Onto an Already-Deployed Customer Machine

Machines that were set up before `health.py`/`health_reporter.py`/the SendGrid switch/
`erp_send_state` existed need more than a bare `git pull` to keep working. Do these **in order**,
*before* letting `update_scripts.bat` pull the new code — `main.py`/`db.py`/`health_reporter.py` all
assume the pieces below are already in place, and will fail (loudly, via `controlled_exit`/a hard
`exit(1)` on a missing credential, or just an "invalid object name" SQL error) if pulled first:

1. **Run [sql/erp_send_state.sql](sql/erp_send_state.sql) against that machine's database.** Check
   first whether it already has some version of this table — **Sun Hydraulics and Spare Parts had an
   earlier, separately-developed version of this fix hand-applied outside this repo**; verify their
   existing `erp_send_state` matches this schema before assuming a plain `CREATE TABLE` will apply
   cleanly, rather than re-running the migration blindly.
2. **Re-run `setup_credentials.py` (or `collect_config.py`)** to add the SendGrid API key, and the
   health reporter shared secret if this machine predates the health dashboard. Outbound email now
   goes through SendGrid (Office 365 SMTP was unreliable and has been dropped) — `email()` in
   `utils.py` calls `credentials.get_sendgrid_api_key()`, which hard-exits the whole process the next
   time `main.py` tries to send a notification if the key isn't in Credential Manager yet.
3. **Fill in / verify the `[health]` section of `config.ini`** (`client_name`, `endpoint_url`).
4. **Add the third, independent Task Scheduler entry for `health_reporter.py`** if this machine
   doesn't already have one (every 15 minutes, runs whether user is logged on or not).
5. Only then pull the updated repo, and test both `main.py` actions manually before trusting the
   scheduled tasks with it.

---

## Environment Variables

| Variable | Required | Description | Example |
|---|---|---|---|
| `SQL_SERVER_NAME` | Yes | SQL Server instance | `MATRIX1\SQLEXPRESS` |
| `SQL_DB_NAME` | Yes | Matrix database name | `ATTC` |
| `P21_CUSTOMER_ID` | Yes | AFI P21 customer ID | `1002050` |
| `P21_SHIP_TO_ID` | No | Ship-to ID (only if multiple exist) | `1002050` |
| `SUPPLIER_KEY` | No | AFI supplier key in Matrix (default: 1) | `2` |

Credentials (P21 API password, SendGrid API key, health reporter secret) are **not** stored in
environment variables — they are stored in Windows Credential Manager via `setup_credentials.py`
(`credentials.py` reads them at runtime; never hardcode a secret directly in source).

---

## Health Reporter

`health_reporter.py` runs on its **own** Task Scheduler entry, separate from Price Sync and Auto
Orders, so it keeps reporting even if `update_process` itself hangs, crashes, or its scheduled task
silently stops running.

- `main.py` and `db.py` write structured events to a local `health_state.db` (SQLite) via `health.py`
  at key moments: run completion/failure, partial orders with dropped items, and DB errors.
- `health_reporter.py` drains those events every 15 minutes and POSTs them to the central dashboard
  endpoint configured in `config.ini` under `[health]`.
- See [VMI_Health_Dashboard_Plan.md](VMI_Health_Dashboard_Plan.md) for the full design and
  [INTEGRATION_NOTES.md](INTEGRATION_NOTES.md) for the wiring details.

---

## Duplicate-Order Guard (`erp_send_state`)

`ent_po_headers.send_erp` alone isn't a safe guard against resubmission: if the script crashes, loses
network, or P21 times out *after* an order was actually submitted but *before* `send_erp` gets set to
`1`, the next run would pick the same PO back up and submit it to P21 a second time.

`erp_send_state` (see [sql/erp_send_state.sql](sql/erp_send_state.sql)) is a second, independent
guard against exactly that:

1. Before calling P21, `mark_inflight()` inserts a `po_key` row into `erp_send_state` with
   `status = 'inflight'`.
2. `get_orders()` only ever returns POs that are **both** `send_erp = 0` **and** absent from
   `erp_send_state` — so a PO stuck mid-submission is never picked up again automatically.
3. Once the outcome is known (`success`, `partial`, or an explicit `error` response from P21),
   `clear_inflight()` removes the row. If the submission call itself raises an exception, the row is
   deliberately left behind — the outcome is unknown, so leaving the guard in place is safer than
   risking a duplicate.
4. `get_stale_inflight()` flags any row still `inflight` after an hour and `orders()` emails an alert
   — a human needs to check P21 and either delete the row (nothing was sent) or set `send_erp = 1`
   manually (it was sent). `health_reporter.py`'s `get_stale_pending_orders()` surfaces the same
   signal on the dashboard every 15 minutes.

---

## Known Client Machines

| Client | Server | Database | Customer ID | Ship To ID | Supplier Key |
|---|---|---|---|---|---|
| American Torch Tip | `MATRIX1\SQLEXPRESS` | `ATTC` | 1002050 | — | 1 |
| Sun Hydraulics | `MATRIX1\SQLEXPRESS` | `SunHyd` | 1005426 | TBD | 1 |
| Fillauer | `DLS1\SQLEXPRESS` | `Fillauer` | 1010934 | — | 1 |
| C&C | — | — | — | — | 1 |
| Spare Parts | — | — | — | — | — |

---

## Usage

```
python main.py                  # Price sync (default)
python main.py -a orders        # Auto order submission
python main.py -a orders -q     # Submit as quotes
python main.py -l debug         # Enable debug logging
```

---

## Making Changes

All fixes and updates go into this repo — **never patch scripts directly on a machine**.
Monthly scheduled tasks on each machine run `update_scripts.bat` to pull the latest version automatically.
