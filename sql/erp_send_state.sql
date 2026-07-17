-- VMI Update Process - erp_send_state migration
--
-- Run this ONCE against the client's own Matrix SQL Server database (the
-- same database referenced by that machine's config.ini [database]
-- sql_db_name) BEFORE deploying a db.py/main.py that references this table
-- - db.py's get_orders()/mark_inflight()/clear_inflight()/get_stale_inflight()
-- will fail with a "invalid object name" error otherwise.
--
-- Purpose: prevents duplicate P21 order submissions. If the script crashes,
-- loses network, or P21 times out after an order was already submitted but
-- before send_erp gets set to 1 on ent_po_headers, the PO would otherwise be
-- picked up and resubmitted on the next run. This table is a second guard,
-- independent of send_erp: a PO is only eligible for submission if it is
-- BOTH send_erp = 0 AND absent from this table.
--
-- Before running: check whether this table already exists on this machine
-- (SunHyd and Spare Parts had an earlier, separately-developed version of
-- this fix applied directly - verify their existing erp_send_state matches
-- this schema before rolling out the shared db.py/main.py to them, rather
-- than re-running this blindly).

CREATE TABLE dbo.erp_send_state (
    po_key     INT NOT NULL PRIMARY KEY,
    status     VARCHAR(20) NOT NULL,  -- only 'inflight' is currently written; rows are deleted, not updated, once the outcome is known
    updated_at DATETIME NOT NULL DEFAULT GETDATE()
);
