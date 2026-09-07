# Tasks — Full Cloud POS System (this cycle: Stock Quantity Adjustments)

Run: `20260907-1646` — ordered (merge order), one commit per task. Tasks 1–2 by
Engineer, tasks 3–4 by QA (per the engineer brief: "leave tests for the QA
role"). Each task touches a DISJOINT set of files so branches can be developed
in parallel and merged without conflicts. Input: target feature "full cloud POS
system" narrowed by `ENGINE_PLAN/20260907-1207/NEXT-CYCLE.md` (approved).

- Implement the whole store-side of the movement ledger in `pos/store.py`: set per-connection SQLite PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`) in `init_db` before the DDL block, add the append-only `stock_movements` table (`id`, `item_id` FK → `items(id)`, `delta` INTEGER `CHECK (delta != 0)`, `movement_type` TEXT `CHECK IN ('in','out')`, `created_at` default now) plus `idx_movements_item` on `item_id` via idempotent additive DDL, add `ItemNotFoundError(ValueError)`, `adjust_quantity(conn, item_id, delta, movement_type)` (validates movement_type in/out, non-zero int delta whose sign matches the type, item exists else `ItemNotFoundError`, non-negative resulting on-hand; inserts the movement row and updates `items.quantity` inside one `with conn:` transaction; returns the new on-hand), and `list_movements(conn, item_id=None)` as the audit read helper; add the WAL sidecars (`stock.db-wal`, `stock.db-shm`) to `.gitignore`; re-run the existing suite (must stay 15 green). Files: `pos/store.py`, `.gitignore`.
- Add the HTTP surface on `pos/server.py`: a `POST /items/{id}/adjust` route (form fields `delta` signed integer, `movement_type`) that parses/validates via new `parse_delta`/`parse_movement_type` helpers, calls `store.adjust_quantity`, replies `303 See Other → /` on success, `400` with a clear message on invalid input or net-negative/sign-mismatch, `404` on `ItemNotFoundError` or any other path; render an inline "Adjust" form (delta + movement-type select) per item row on `GET /`; and replace the manual `commit()`/`rollback()` in `_add_item` with `with conn:`. Files: `pos/server.py`.
- QA gate (store layer): add `tests/test_movements.py` covering the ledger + `adjust_quantity` happy path (movement row inserted, returned/new quantity, `list_movements` rows and item filter, `quantity == SUM(delta)` after in/out), rejections (unknown item → `ItemNotFoundError`, net-negative → `ValueError` with no row and unchanged quantity, sign mismatch, zero/non-int delta, bad movement_type), and persistence across a reopen. Files: `tests/test_movements.py`.
- QA gate (HTTP + docs): add `tests/test_adjust_http.py` end-to-end coverage of `POST /items/{id}/adjust` (valid in/out → 303 → updated quantity on `GET /`, net-negative → 400 with quantity unchanged and no movement row, unknown item id → 404, bad/missing delta or movement_type → 400, unknown path → 404, persistence across a reopen); update `TESTING.md` (coverage + status) and the README feature blurb; run the full suite and confirm green, recording the result. Files: `tests/test_adjust_http.py`, `TESTING.md`, `README.md`.

## Traceability

- Task 1 → NEXT-CYCLE tasks 1–3 + research Unknowns 1–2 (SQLite concurrency
  precondition, movement-ledger design, net-negative enforcement).
- Task 2 → NEXT-CYCLE task 4 (HTTP adjust route + per-row form) and
  NEXT-CYCLE refinement 1 (`with conn:` in `_add_item`).
- Tasks 3–4 → NEXT-CYCLE task 5 (QA gate: happy path, rejections, invariant,
  persistence, HTTP statuses, `TESTING.md`).

## Status

- Not started this session; awaiting implementation and QA gate.