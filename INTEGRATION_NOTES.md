# Wiring health.py into the standardized codebase

Apply these as edits to the existing standardized `main.py` and `db.py`
(not shown here since this project only has the pre-standardization
versions — apply against your `VMI-Standardized` repo).

## main.py

Add the import:
```python
import health
```

### In `items()`, replace the final block:
```python
    close_db_conn(l_db_conn)
    stop_log('Update process', l_succ_cnt, l_tot_cnt)

    health.record_run('items', 'success', l_succ_cnt, l_tot_cnt)

    email('Matrix Auto Price Changes for ' + get_customer_name())
    rename_log()
```
(Wrap the whole function body in try/except so a hard crash still records
`'error'` before re-raising — optional but closes the "silent full crash"
gap:)
```python
def items():
    l_tot_cnt = 0
    l_succ_cnt = 0
    start_log('Update process')
    try:
        l_db_conn = connect_db()
        l_cursor = l_db_conn.cursor()
        l_rows = get_items(l_cursor)
        for row in l_rows:
            ...
        close_db_conn(l_db_conn)
        stop_log('Update process', l_succ_cnt, l_tot_cnt)
        health.record_run('items', 'success', l_succ_cnt, l_tot_cnt)
        email('Matrix Auto Price Changes for ' + get_customer_name())
        rename_log()
    except Exception:
        health.record_run('items', 'error', l_succ_cnt, l_tot_cnt)
        raise
```

### In `orders()`, at the existing status branches:
```python
            if l_status == 'error':
                log_error('Submitting Order to API failed: ...')
                email('Matrix Auto Order Error for ' + get_customer_name(), ...)
                health.record_event('order_error', l_message, str(l_order.po_code or ''))

            elif l_status == 'partial':
                l_order_no = l_response
                update_order(l_cursor, l_order.po_key)
                log_info('Order created with skipped items: ...')
                log_error('Items skipped in order ' + l_order_no + ':\n' + l_message)
                email('Matrix Auto Order Item Exception(s) for ' + get_customer_name(), l_message, False)
                health.record_event('partial_order', l_message, str(l_order.po_code or ''))
                l_succ_cnt += 1

            else:
                ...
```
And at the end of `orders()`, mirror the same `record_run('orders', ...)`
pattern used in `items()` above.

## db.py

In `controlled_exit()`:
```python
def controlled_exit(p_message):
    log_error(p_message)
    log_shutdown()
    health.record_event('db_error', p_message)
    email('Matrix Auto (DB Error) for ' + get_customer_name())
    rename_log()
    exit()
```
(add `import health` at the top of `db.py` as well)

## config.ini.template additions

```ini
[health]
client_name = 
endpoint_url = https://<your-azure-vm>/health/intake
```
`reporter_secret` is NOT stored in config.ini — add it to
`collect_config.py` / `credentials.py` the same way the P21 API password is
handled today (keyring, Windows Credential Manager), and expose it via
`credentials.get_health_reporter_secret()`.

## .gitignore addition
```
health_state.db
logs/health_reporter.log
```

## New Task Scheduler entry (per machine, separate from the two existing ones)

- **Name:** `VMI Health Reporter`
- **Program:** same Python path used for the other two tasks
- **Arguments:** `health_reporter.py`
- **Start in:** `C:\update_process`
- **Schedule:** every 15 minutes, indefinitely (not just a 12-hour window like Auto Orders)
- **Run whether user is logged on or not:** Yes
