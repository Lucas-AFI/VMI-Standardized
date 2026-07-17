# VMI Health Dashboard — Project Plan

## Problem

~150 client sites run `update_process` (Python) on-premise, handling VMI
auto-pricing and auto-ordering against P21 via API. Failures are currently
caught reactively — someone notices a duplicate order, a stuck Task
Scheduler job, or a client calls about a missing item — with no central
visibility into which of the ~150 machines are healthy at any given time.

Known failure modes already encountered in production:
- Task Scheduler / Python path breakage (e.g. the `pythoncore-3.12-64`
  migration) causing a machine to silently stop running entirely.
- Duplicate P21 sales orders from interrupted/retried submissions.
- **Silent item drops**: an order submits successfully to P21, but one or
  more line items get skipped along the way (the "gloves SKU" incident) —
  the order looks successful in every system of record even though data
  was lost.

## Goal

A central dashboard showing, for every client site: is it running, is it
stuck, and has anything gone wrong — without needing to notice a symptom
first.

## Architecture Decision: Push, Not Pull

Client machines are **outbound-only** by design (they reach out to fetch
prices/inventory and submit orders to P21). There is no inbound network
access into any client's internal network, and getting it would mean
asking every client individually to open a firewall exception — a
per-customer security/trust negotiation, not a technical task, and
unrealistic at ~150 sites.

**Decision: architecture is push-based.** Each client machine reports its
own health out to a central endpoint, rather than a central system
reaching in to query it.

## Local Reporter (per client machine)

- **A small, standalone script**, deployed to each of the ~150 client
  machines, run via its **own separate Task Scheduler entry** — explicitly
  *not* bolted onto the end of `update_process` itself. This is
  deliberate: the reporter's entire purpose is to catch failures *in*
  `update_process`'s own logic, so it has to keep running independently
  even if `update_process` hangs or crashes.
- **Interval: every 15 minutes.**
- Reads from tables that **already exist** on each machine — no new
  tables need to be created on client databases:
  - `erp_send_state` — an in-flight/duplicate-prevention guard already
    built for this pipeline. Tracks orders submitted-but-not-yet-confirmed.
    A `get_stale_inflight()` function already exists, flagging anything
    stuck in-flight for more than an hour — this is close to a ready-made
    "is something stuck" signal.
  - `ent_po_headers` — full order history. Key confirmed field: `SEND_ERP`
    — `0` means not yet submitted (still pending, picked up by
    `get_orders()` each run), `1` means an attempt was made and P21
    accepted at least part of it (set after **both** full success and
    partial success — this nuance matters, see below).
- Packages a small JSON payload and POSTs it to the central Azure endpoint.

## The Item-Drop ("Gloves SKU") Detection Problem

This is the trickiest failure mode and deserves its own callout.

Raw "item not found" events are **not** inherently errors — SKU mapping
mismatches between AFI's and a customer's SKUs happen routinely and are
expected noise. The real failure is narrower: **an item-not-found event
where the order it was part of still submitted to P21 successfully, with
the bad SKU silently dropped.**

Source review of `main.py`/`db.py` found this is already a distinct,
known code path — `check_order()` can return `'partial'`, at which point
the existing code:
1. Logs the skipped items via `log_error()`
2. Sends a warning email
3. **Still** calls `update_order()`, setting `SEND_ERP = 1` — meaning the
   order is marked done and will **never be retried**, despite missing items
4. Still counts as a success in the script's own run counter

**There is no database field anywhere that distinguishes "sent cleanly"
from "sent with dropped items."** This event currently only exists in the
log file and the email it triggers. This means detecting it centrally
requires one of two approaches:
- **(a) Parse `app_*.log` for the partial/skipped-item message pattern**,
  matched to the relevant PO code — read-only, no changes to
  `update_process` needed, but more fragile (log format drift breaks it).
- **(b) Modify `update_process` itself** to write this event to something
  structured (a small local table, or directly into the heartbeat payload)
  at the exact moment `check_order()` returns `'partial'` — more reliable,
  effectively a real bug fix in its own right, but requires touching and
  redeploying code across all ~150 sites.

**Open decision, not yet made:** which of these two approaches to take.
Needs `utils.py` (specifically `check_order()`) to see the exact message
format regardless of which path is chosen.

## Central Storage (Azure VM)

Single central SQL database on the **existing** Azure VM (same one already
hosting the current Flask tools) — no new infrastructure needed.

**Two-table model** (chosen instead of logging every 15-min checkin,
since ~150 sites × 15-min intervals would mostly be redundant "all clear"
noise):

1. **`client_status`** — one row *per client*, upserted (not inserted)
   every 15 minutes. Stays at ~150 rows regardless of runtime.
   - Fields: `client_name`, `last_heartbeat_at`, `current_status`
     (`operational` / `error`), `last_error_summary`.
   - This table catches **silent failures**: if `last_heartbeat_at` hasn't
     updated recently, that machine isn't reporting at all — independent
     of whether an explicit error was ever flagged. This is the safety net
     for "Task Scheduler broke" or "network is down," not just app-level bugs.

2. **`error_events`** — append-only, a row inserted **only** when an actual
   error condition is detected (never for routine all-clear checkins).
   - Fields: `client_name`, `detected_at`, `error_type`, `detail/summary`,
     `related_po_key` (if applicable).
   - This is the real historical/trend table — the one worth mining later
     for patterns across clients.

**Default dashboard state:** every client shows "operational" unless
`client_status` shows either a stale heartbeat or an error state. Error
history view pulls from `error_events`.

**What counts as an error** (candidates confirmed so far):
- Heartbeat itself goes stale/missing (detected via `client_status`, not
  `error_events`)
- Stale in-flight orders in `erp_send_state` (already has a built-in
  detector: `get_stale_inflight()`)
- Order submission fully failed (`check_order()` returns `'error'`)
- Order submitted with items silently dropped (`check_order()` returns
  `'partial'`) — the gloves SKU case, see above

**Retention:** `client_status` never needs pruning (stays ~150 rows).
`error_events` retention length (forever vs. a bounded window like 1 year)
is not yet decided.

## Open Items / Next Steps

- [ ] Decide: modify `update_process` to capture the partial/drop event
      directly, or rely on log parsing (see Item-Drop section above)
- [ ] Pull `utils.py` / `check_order()` to get the exact partial-result
      message format
- [ ] Get `app_*.log` format and on-disk location/rotation behavior
- [ ] Get `ent_item_master` schema (relevant fields only)
- [ ] Get the full ~150-site client list (only a handful confirmed so far:
      American Torch Tip, Sun Hydraulics, SpareParts, SunHyd, Fillauer, Ferrera)
- [ ] Audit per-client network constraints (outbound HTTPS reliability,
      any client-specific firewall/proxy quirks)
- [ ] Confirm Azure VM has capacity for the new endpoint + DB table
- [ ] Decide `error_events` retention length
- [ ] Design the actual `client_status`/`error_events` table DDL and the
      Flask intake route

## Notably Deprioritized

- `STATUS_KEY` and `IS_SEND` on `ent_po_headers` — confirmed via source
  review to be unrelated to this pipeline (likely part of a separate
  manual PO approval workflow within Matrix). Not worth further
  investigation for this project.
- Pull-based/central-query architecture — ruled out due to no inbound
  network access to client sites.
