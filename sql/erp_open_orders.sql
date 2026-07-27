-- VMI Update Process - erp_open_orders migration
--
-- Run this ONCE against the client's own Matrix SQL Server database (same
-- database as erp_send_state.sql, referenced by that machine's config.ini
-- [database] sql_db_name) BEFORE deploying a db.py/main.py that references
-- this table - get_open_orders()/record_open_order()/clear_open_order() in
-- db.py will fail with an "invalid object name" error otherwise.
--
-- Purpose: track P21 order numbers after successful submission so
-- check_open_orders() in main.py's orders() can periodically ask P21 for
-- their current status and flag ones that have sat open (not Completed,
-- Cancelled, or Deleted) for longer than STALE_OPEN_ORDER_DAYS - previously
-- nothing recorded the P21 order number anywhere once send_erp was set, so
-- there was no way to check back on an order's fate later.
--
-- A row is removed once P21 reports the order Completed/Cancelled/Deleted -
-- this table only ever holds currently-open orders, not history (the
-- 'stale_open_order' health event is what leaves the durable record, same
-- pattern as 'stale_inflight_order').

CREATE TABLE dbo.erp_open_orders (
    po_key       INT NOT NULL PRIMARY KEY,
    po_code      VARCHAR(50) NULL,
    order_no     VARCHAR(50) NOT NULL,
    submitted_at DATETIME NOT NULL DEFAULT GETDATE()
);
