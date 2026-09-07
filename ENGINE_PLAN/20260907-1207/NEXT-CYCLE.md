# NEXT-CYCLE — Stock Quantity Adjustments (In/Out)

- Run: `20260907-1207` (proposal for the *next* cycle)
- Current state: **stock items list + add item** shipped and APPROVED (`ENGINE_STATE/review.md`, AC1–AC6 all met; 15 tests green). No blocking issues.
- Target of next cycle: the PRD's first "Future" item — **quantity adjustments (stock in/out)** — which is also the foundation for the eventual checkout flow. Grounded in `ENGINE_RESEARCH.md` (HIGH-value Unknown 1, MEDIUM-value Unknown 2).

## Refinements (small, to the just-shipped code)

1. **Formalize transactions with `with conn:` in `pos/server._add_item`.** Replace the manual `conn.commit()` / `except ... conn.rollback()` in the `try/finally` block with the `with conn:` context manager (auto-commit on success, auto-rollback on exception). Behavior is unchanged — verify by re-running the full suite (must stay 15 green). This removes the last manual transaction and makes the next cycle's writes consistent. `[research]` Unknown 2.

## New tasks (next cycle, ordered by value)

1. `[research]` **Add per-connection SQLite PRAGMAs in `pos/store.init_db`** (WAL, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`) and keep the one-connection-per-request pattern. Required precondition before any second write path; prevents flaky `database is locked` when adjustments ship. Verify with existing suite still green. (Unknown 2.)

2. `[research]` **Add `stock_movements` ledger table** (`id`, `item_id INTEGER NOT NULL REFERENCES items(id)`, `delta INTEGER NOT NULL`, `movement_type TEXT NOT NULL CHECK IN ('in','out')`, `created_at TEXT DEFAULT (datetime('now'))`) via a new schema-migration path in `init_db`; add an index on `item_id`. Keep `items.quantity` as the display cache, updated **inside the same transaction** as each movement insert so they never diverge. (Unknown 1.)

3. `[research]` **Add `pos.store.adjust_quantity(conn, item_id, delta, movement_type)`**: validates the item exists and that the resulting on-hand is non-negative, inserts a movement row, updates `items.quantity` in one `with conn:` transaction; returns the new on-hand; raises `ValueError` on unknown item or negative result (net-negative stock is rejected). Add the corresponding `list_movements` read helper for testing/audit. (Unknown 1.)

4. **Add HTTP surface for adjustments**: a `POST /items/{id}/adjust` route (fields `delta` signed integer, `movement_type`) on `pos/server.POSHandler` — validate, call `adjust_quantity`, `303 See Other → /` on success, `400` with a clear message on invalid/negative-result input, `404` for unknown item or other paths. Add an "Adjust" form/button per row in `GET /`. (Unknown 1.)

5. **QA gate (next cycle)**: extend `tests/` with happy-path + rejection coverage for the ledger and adjust flow — movement row inserted, `items.quantity` matches `SUM(delta)` after in/out, net-negative rejection inserts no row and leaves quantity unchanged, unknown-item → 400/404, and persistence across a reopen; update `TESTING.md` command/notes; run full suite green and record status. (Unknown 1.)

## Explicitly deferred (do NOT do this cycle)

- Checkout/sale flow (a sale = an `out` movement; build only once the ledger + adjust are stable).
- Edit/delete items, JSON REST API, auth, deployment — per PRD non-goals / Future.

## Traceability

- Task 1 → `ENGINE_RESEARCH.md` Unknown 2 (concurrency precondition).
- Tasks 2–4 → `ENGINE_RESEARCH.md` Unknown 1 (movement-ledger design) + PRD Future "quantity adjustments".
- Task 5 → QA brief + research recommendations (audit invariants, net-negative enforcement).
- Refinement 1 → research Unknown 2 (formalize `with conn:`); also addresses review's non-blocking note on manual transaction handling.