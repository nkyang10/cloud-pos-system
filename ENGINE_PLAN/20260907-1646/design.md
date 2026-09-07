# Design — Full Cloud POS System (Stock Quantity Adjustments)

- Run: `20260907-1646`
- Approved input: the target feature "full cloud POS system", narrowed by branch
  context (`ENGINE_PLAN/20260907-1207/NEXT-CYCLE.md` — this cycle's increment is
  **stock quantity adjustments in/out** on a movement ledger).

## Tech stack (unchanged)

- Python 3.8+, **standard library only** — no `pip install`, no third-party deps.
- `http.server` (stdlib) for the web layer; `sqlite3` for persistence.
- Entry point `run.py` (unchanged: `PORT` env override, default 8000).
- Tests via stdlib runner `python -m unittest discover -s tests`.

## Module / file layout

```
run.py                    # unchanged
pos/store.py              # + PRAGMAs, stock_movements DDL/index, ItemNotFoundError,
                          #   adjust_quantity, list_movements (this cycle)
pos/server.py             # + POST /items/{id}/adjust, per-row Adjust form on GET /,
                          #   `with conn:` transaction in the add handler (refinement)
tests/test_store.py       # existing store tests (unchanged, must stay green)
tests/test_server.py      # existing handler tests (unchanged, must stay green)
tests/test_run.py         # existing run.py tests (unchanged)
tests/test_movements.py   # NEW QA: store-level ledger + adjust_quantity tests
tests/test_adjust_http.py # NEW QA: end-to-end HTTP adjust tests
.gitignore                # + stock.db-wal, stock.db-shm (WAL sidecar files)
README.md                 # QA gate: one-line feature update
TESTING.md                # QA gate: coverage + status update
```

## Data model

`items` (existing, unchanged schema):

```
id          INTEGER PRIMARY KEY AUTOINCREMENT
name        TEXT NOT NULL UNIQUE COLLATE NOCASE
price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
quantity    INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0)
```

`stock_movements` (new, append-only ledger):

```
id            INTEGER PRIMARY KEY AUTOINCREMENT
item_id       INTEGER NOT NULL REFERENCES items(id)
delta         INTEGER NOT NULL CHECK (delta != 0)
movement_type TEXT NOT NULL CHECK (movement_type IN ('in','out'))
created_at    TEXT NOT NULL DEFAULT (datetime('now'))
```

- Index on `item_id` for audit/list-by-item queries:
  `CREATE INDEX IF NOT EXISTS idx_movements_item ON stock_movements(item_id)`.
- **Convention:** `delta` is signed — positive for `in`, negative for `out` —
  and its sign must match `movement_type` (enforced in `adjust_quantity`).
- **Invariant (AC5):** for each item, `items.quantity == SUM(stock_movements.delta)`.
  The ledger is the source of truth; `items.quantity` is a display cache updated
  in the same transaction as each movement insert so they never diverge.

## Schema migration

`init_db` keeps a single idempotent DDL block (`CREATE TABLE IF NOT EXISTS` /
`CREATE INDEX IF NOT EXISTS`) covering both tables. Adding `stock_movements` is
purely additive — an existing `stock.db` gains the new table/index automatically
on the next start; no data migration or destructive change.

## Concurrency (precondition, AC1)

Per-connection PRAGMAs in `init_db`, applied **before** the DDL block:

- `journal_mode=WAL`
- `foreign_keys=ON`
- `busy_timeout=5000`
- `synchronous=NORMAL`

Keeps the existing one-connection-per-request pattern (`init_db` per request,
closed in a `finally`). Required before any second write path; prevents flaky
`database is locked` once adjustments ship. The WAL sidecar files
(`stock.db-wal`, `stock.db-shm`) are added to `.gitignore`.

## Key APIs (store — `pos/store.py`)

```
adjust_quantity(conn, item_id, delta, movement_type) -> int
```
Validates, in order:
- `movement_type` is `'in'` or `'out'` (else `ValueError`);
- `delta` is an int (bool excluded) and non-zero (else `ValueError`);
- the sign of `delta` matches `movement_type` — `in` → `delta > 0`,
  `out` → `delta < 0` (else `ValueError`);
- the item exists — else raises `ItemNotFoundError` (a `ValueError` subclass)
  so the HTTP layer can map it to `404` without message sniffing;
- the resulting on-hand `current_quantity + delta >= 0` (else `ValueError`).

Then, **inside one `with conn:` transaction** (auto-commit on success,
auto-rollback on exception — satisfies AC4), it inserts the movement row and
updates `items.quantity = items.quantity + delta`. Returns the new on-hand int.

```
list_movements(conn, item_id=None) -> list[dict]
```
Audit read helper. Rows `{id, item_id, delta, movement_type, created_at}` ordered
by `id` ascending; optionally filtered by `item_id`.

`ItemNotFoundError(ValueError)` — new exception class in `pos/store.py`.

## Key APIs (HTTP — `pos/server.py`)

- `POST /items/{id}/adjust` (new route on `POSHandler`):
  - Form fields: `delta` (signed integer, e.g. `10`, `-2`), `movement_type`
    (`in`/`out`).
  - Parse/validate with `parse_delta(raw)` (signed non-zero int) and
    `parse_movement_type(raw)` (`in`/`out`); format errors → `400` HTML with a
    clear message and a back link.
  - Calls `store.adjust_quantity`; on success → `303 See Other → /`.
  - Exceptions: `ItemNotFoundError` → `404`; `ValueError` (net-negative, sign
    mismatch) → `400`; any other path → `404`.
- `GET /` renders, per item row, an inline "Adjust" form (numeric `delta` input
  + `movement_type` select) posting to `/items/{id}/adjust`, plus the quantity
  cell as today.
- Refinement (from NEXT-CYCLE): `_add_item`'s manual `commit()`/`rollback()` is
  replaced with `with conn:` (auto-commit on success, auto-rollback on
  `ValueError`); behavior is unchanged and the existing add tests stay green.
- Helpers stay in `pos/server.py`; all user input is HTML-escaped before render.

## Transaction ownership

- New store writes (`adjust_quantity`) manage their own transaction with
  `with conn:` — atomicity is guaranteed at the store layer regardless of caller.
  `add_item` keeps its existing caller-commits contract (unchanged, so the 15
  existing tests are untouched).
- Server stays thin: parse/validate form input, delegate to the store, map
  exceptions to status codes.

## Assumptions (stated, not blocking)

- `delta` is authoritative and signed; its sign must match `movement_type`. The
  UI sends e.g. `10`/`in` or `-2`/`out`; a mismatch is a `400`, not a silent fix.
- The ledger is append-only: no update/delete of movement rows this cycle; no
  ledger page in the UI (`list_movements` is for tests and future audit UI).
- SQLite file remains `stock.db` (gitignored, now with its WAL sidecars).
- Tests override the class attribute `POSHandler.db_path` and restore it in
  `tearDown` (existing pattern; safe for the sequential stdlib runner).
- QA owns all new tests (`tests/test_movements.py`, `tests/test_adjust_http.py`)
  and test/docs updates (`TESTING.md`, `README.md`) per the engineer brief
  ("leave tests for the QA role").

## Acceptance mapping

- AC1 → store PRAGMAs in `init_db`; AC2 → `stock_movements` DDL + index;
  AC3 → `adjust_quantity`/`list_movements`/`ItemNotFoundError`; AC4 →
  single-`with conn:` write + reject-before-write validation; AC5 → invariant
  test (`quantity == SUM(delta)`); AC6 → HTTP route + per-row form; AC7 →
  persistence test; AC8 → QA suite green + `TESTING.md`.

## Risks / open questions

- Sign/type duplication (`delta` + `movement_type`) invites mismatches — mitigated
  by rejecting mismatches with a clear `400` and by documenting the convention.
- WAL keeps `stock.db-wal`/`stock.db-shm` around while a connection is open —
  handled via `.gitignore` and per-request connection close.
- Adjusting quantity has no write/write race protection beyond WAL + busy_timeout
  this cycle (single user on localhost); multi-station sync is a TARGETS.md
  future item.
