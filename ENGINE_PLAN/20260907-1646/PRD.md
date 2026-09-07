# PRD — Full Cloud POS System (this cycle: Stock Quantity Adjustments, In/Out)

- Run: `20260907-1646`
- Target feature: **full cloud POS system** (long-term product vision per
  `ENGINE_STATE/TARGETS.md`). This cycle ships the next shippable slice of that
  vision: **stock quantity adjustments (stock in / stock out)** — the
  movement-ledger foundation that the checkout/sale flow will be built on.
- Input: no `ENGINE_PLAN/20260907-1646/NEXT-CYCLE.md` exists on this branch, so
  the target feature itself is the approved input. Branch context narrows it:
  `ENGINE_PLAN/20260907-1207/NEXT-CYCLE.md` (the prior cycle's proposal for
  *this* cycle, approved via `ENGINE_STATE/review.md`, 15 tests green) targets
  quantity adjustments as the next increment and lists checkout as explicitly
  deferred until the ledger is stable.

## Problem

The POS app can list and add stock items, but a cashier has no way to change
quantities: no restock (stock in), no sale/correction (stock out), and no audit
trail — today `items.quantity` is a bare, mutable column with no record of *why*
it changed. A "full cloud POS system" cannot function without inventory
movements, and checkout will be a special case of "stock out".
This cycle adds the **movement-ledger** foundation: an append-only
`stock_movements` table plus an adjust in/out flow, along with the concurrency
preconditions (WAL, busy timeout, foreign keys) that any second write path
requires.

## MUST-HAVE happy path

1. User starts the app locally: `python run.py`, opens `http://127.0.0.1:8000`.
2. The stock-items list shows each item with an inline "Adjust" control
   (signed delta + movement type in/out).
3. Cashier stocks in `+10` on an item and submits; the list shows the new
   quantity immediately.
4. Cashier stocks out `-2` on the same item and submits; the list shows the
   updated quantity immediately.
5. Every adjustment is recorded in the append-only `stock_movements` ledger
   (movement_type `in`/`out`, delta, timestamp).
6. A stock-out that would drive on-hand below zero is rejected with a clear
   error page and records nothing.
7. Adjustments and the ledger persist across a server restart.

## Non-goals (this cycle)

- No checkout/sale flow, receipts, or payments (a sale = an `out` movement;
  built only once the ledger + adjust are stable, per NEXT-CYCLE "deferred").
- No edit/delete of items or of movement rows; no ledger history page in the UI
  (`list_movements` exists as a store/audit helper for tests and future use).
- No authentication, user accounts, multi-store/tenant, or real cloud hosting —
  "cloud" remains a vision item; the app stays locally runnable.
- No JSON REST API, no front-end framework, no barcode/QR/voice, no
  Docker/CI/deployment.

## Acceptance criteria

- **AC1** — `pos.store.init_db` sets the per-connection SQLite PRAGMAs
  (`journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`,
  `synchronous=NORMAL`) before creating the schema; the existing suite (15
  tests) stays green (regression guard for the concurrency precondition).
- **AC2** — A `stock_movements` table exists (`id`, `item_id` FK →
  `items(id)`, `delta` INTEGER `CHECK (delta != 0)`, `movement_type` TEXT
  `CHECK IN ('in','out')`, `created_at` default now) with an index on
  `item_id`, created idempotently via additive DDL so an existing `stock.db`
  migrates on next start.
- **AC3** — `pos.store.adjust_quantity(conn, item_id, delta, movement_type)`
  inserts the movement row and updates `items.quantity` **in the same
  transaction** and returns the new on-hand; unknown item raises
  `ItemNotFoundError` (a `ValueError` subclass); a non-zero-integer/sign-mismatch
  delta or a net-negative result raises `ValueError` with a clear message.
- **AC4** — Rejected adjustments record **no** movement row and leave
  `items.quantity` unchanged.
- **AC5** — Invariant: after any sequence of adjustments, each adjusted item's
  `quantity` equals `SUM(delta)` of its movement rows (the ledger is the source
  of truth; `items.quantity` is the display cache).
- **AC6** — HTTP surface: `POST /items/{id}/adjust` (fields `delta` signed
  integer, `movement_type`) → `303 See Other → /` on success; `400` with a
  clear message on invalid input or a net-negative result; `404` for an unknown
  item or any other path. `GET /` renders an "Adjust" form per item row and the
  updated quantity.
- **AC7** — Adjustments and the ledger survive a server restart (SQLite
  persistence).
- **AC8** — QA suite covers the ledger + adjust flow (happy path, rejections,
  invariant, persistence, HTTP statuses); canonical command
  `python -m unittest discover -s tests` runs fully green and `TESTING.md` is
  updated.

## Future (out of scope)

Checkout/sale flow (sale = `out` movement, unit-price snapshotting), edit/delete
items, ledger history UI, realtime sync, auth, and actual cloud deployment.
