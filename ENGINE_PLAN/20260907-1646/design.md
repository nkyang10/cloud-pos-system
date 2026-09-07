# Design — Full Cloud POS System (Cycle 1: Concurrent Cloud Cashiering)

- Run: `20260907-1646`

## Tech stack

- **Python 3.8+ — standard library only.** Same as the shipped baseline (run
  `20260907-1207`): `http.server` (`ThreadingHTTPServer` + `BaseHTTPRequestHandler`) for
  HTTP, `sqlite3` for persistence, `urllib.parse` / `decimal.Decimal` for input parsing.
  Zero third-party dependencies — no `pip install`, runs anywhere Python exists.
- **Why:** the assembler brief mandates the minimal, dependency-light, locally-runnable
  option, and the repo's convention is stdlib-only. The eventual full cloud POS will move
  to Postgres/realtime infra; this cycle keeps the stdlib/SQLite backend but adds the
  concurrency discipline (atomic receipt sequence + conditional stock update + WAL) that
  the final backend will keep.
- **Tests:** stdlib `unittest` (`python -m unittest discover -s tests`), per
  `TESTING.md`.

## Assumptions (stated, not blocking)

- "Cloud POS" this cycle = **one shared server process on the shop network** (bind
  `0.0.0.0`) that all stations' browsers hit; the SQLite file is the single source of
  truth. Real cloud hosting/Postgres/Redis is a Future cycle.
- No auth: the server is trusted on the shop LAN (auth is Future), stated in the PRD
  non-goals.
- Realtime push is deferred; "multi-station" is satisfied this cycle by a **shared
  concurrency-safe backend** where interleaved charges cannot corrupt data — the
  precondition for realtime sync later.
- Money remains integer **cents** (HKD default); totals are computed server-side from
  stored unit prices — never from client-submitted prices. Display reuses the existing
  `format_price` helper unchanged so baseline tests stay green.
- Receipt numbers are minted **server-side** inside the sale transaction (single-writer
  receipt sequence); clients never supply a receipt number. Format `"{year}-{no:04d}"`,
  e.g. `2026-0001`, resetting per calendar year.
- Payment methods are **recorded, not processed**: cash, card, octopus, fps, alipayhk,
  wechatpayhk, payme, store-credit, each with an optional free-text reference.
- `ThreadingHTTPServer` gives one thread per request; SQLite is made safe for that with
  WAL, `busy_timeout`, and short `with conn:` transactions (auto-commit/rollback — the
  `with conn:` discipline the 1207 NEXT-CYCLE proposal already recommended).
- The 1207 NEXT-CYCLE proposal (stock in/out adjustments ledger) is **deferred**, not
  adopted: this cycle's target per the assembler instruction is the full cloud POS
  system, and checkout's atomic stock decrement already establishes the ledger's core
  invariant. Its `with conn:` refinement is folded into the new checkout code.
- Tests are owned by the **QA role** per the engineer brief (tasks 4–5); tasks 1–3 are
  Engineer tasks.

## File/module layout

```
run.py                    # entry point: bind 0.0.0.0:PORT (default 8000) -> shared POS server
pos/
  __init__.py             # empty package marker
  store.py                # SQLite repository + schema init + checkout/sequence/stock logic
  server.py               # http.server handler: register page, charge, receipt pages
stock.db                  # created at runtime (gitignored)
tests/
  test_store.py           # unit tests: store validation, checkout, concurrency, persistence
  test_server.py          # end-to-end: GET /, POST /charge, GET /receipts/{no}, rejections
  test_run.py             # run.py port + host binding (AC1)
  test_checkout.py        # NEW: checkout + concurrency coverage (AC3/AC4/AC6) — QA-owned
TESTING.md                # canonical test command + coverage notes
README.md                 # run/test instructions + feature blurb
```

## Data model

Existing table `items` unchanged (`id, name, price_cents, quantity`). `init_db` gains the
following tables (idempotent additive DDL — existing `stock.db` files migrate on next
start) plus per-connection PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`,
`busy_timeout=5000`, `synchronous=NORMAL`):

Table `receipt_sequences` — single-writer receipt counter:

| column  | type    | constraints        |
|---------|---------|--------------------|
| year    | INTEGER | PRIMARY KEY        |
| last_no | INTEGER | NOT NULL DEFAULT 0 |

Table `transactions` — one row per sale:

| column         | type    | constraints                                |
|----------------|---------|--------------------------------------------|
| id             | INTEGER | PRIMARY KEY AUTOINCREMENT                  |
| receipt_no     | TEXT    | NOT NULL UNIQUE (e.g. `2026-0001`)         |
| receipt_year   | INTEGER | NOT NULL                                   |
| subtotal_cents | INTEGER | NOT NULL CHECK (>= 0)                      |
| total_cents    | INTEGER | NOT NULL CHECK (>= 0)                      |
| payment_method | TEXT    | NOT NULL CHECK IN (allowed set)            |
| payment_ref    | TEXT    | NULL                                       |
| created_at     | TEXT    | DEFAULT (datetime('now'))                  |

Table `transaction_lines` — itemized lines (snapshot name/price so receipts survive
item edits):

| column           | type    | constraints                          |
|------------------|---------|--------------------------------------|
| id               | INTEGER | PRIMARY KEY AUTOINCREMENT            |
| txn_id           | INTEGER | NOT NULL REFERENCES transactions(id) |
| item_id          | INTEGER | NOT NULL REFERENCES items(id)        |
| name             | TEXT    | NOT NULL                             |
| unit_price_cents | INTEGER | NOT NULL                             |
| qty              | INTEGER | NOT NULL CHECK (qty > 0)             |
| line_total_cents | INTEGER | NOT NULL                             |

Indexes: `idx_txn_lines_txn` on `transaction_lines(txn_id)`; the UNIQUE constraint on
`transactions(receipt_no)` is the backstop for the sequence.

## Key APIs

`pos/store.py`:
- `init_db(db_path="stock.db") -> sqlite3.Connection` — as before, plus the new tables,
  PRAGMAs, and `row_factory=sqlite3.Row`.
- `list_items(conn)` — unchanged (register page uses it).
- `add_item(conn, name, price_cents, quantity=0) -> int` — unchanged.
- `checkout(conn, lines, payment_method, payment_ref=None) -> dict` — **the core atomic
  sale**. `lines` is a list of `{item_id, qty}`. In ONE `with conn:` transaction:
  1. Validate every line (item exists, qty is a positive int) and the payment method
     (in the allowed set).
  2. **Atomic stock check-and-decrement** per line:
     `UPDATE items SET quantity = quantity - ? WHERE id = ? AND quantity >= ?`; if 0 rows
     affected → `ValueError("Insufficient stock for ...")` and the transaction rolls back
     (no partial decrements).
  3. Mint the next receipt number: ensure the current-year row exists in
     `receipt_sequences`, then `UPDATE receipt_sequences SET last_no = last_no + 1
     WHERE year = ?`; format `"{year}-{no:04d}"`.
  4. Insert the `transactions` row and its `transaction_lines` (snapshot name +
     `unit_price_cents` from `items`), computing subtotal/total server-side.
  5. Return `{receipt_no, receipt_year, subtotal_cents, total_cents, payment_method,
     payment_ref, created_at, lines:[...]}`. Raises `ValueError` on any validation
     failure (nothing is written).
- `get_transaction(conn, receipt_no) -> dict | None` — transaction + lines for the
  receipt page.
- `list_transactions(conn) -> list[dict]` — receipt_no, created_at, total_cents, ordered
  newest-first.

`pos/server.py` (additions to `POSHandler`):
- `GET /` — register page: item catalog + current-sale ticket form (repeated
  `item_id`/`quantity` fields for multi-line tickets) + payment-method select + charge
  button. Any other path → 404.
- `POST /charge` — parse the urlencoded body, validate via `store.checkout`, on success
  `303 See Other → /receipts/{receipt_no}`; on `ValueError` a `400` HTML page with a
  clear message and a link back; any other path → 404.
- `GET /receipts/{receipt_no}` — HTML receipt (receipt number, date, store header,
  itemized lines qty × unit price, subtotal, total, payment method + reference, cash
  change if cash); unknown receipt → 404.
- `GET /receipts` — HTML list of past transactions.
- Reuses `format_price`, `_page`, `_send_html`, `_read_form_body`, `_path` from the
  baseline.

`run.py`:
- `main()` — bind `ThreadingHTTPServer` on **`0.0.0.0`** (was `127.0.0.1`) so stations on
  the network can connect; `PORT` env override preserved (`_parse_port` unchanged).
- Startup message prints the reachable host.

## Running & testing

- Run: `python run.py` → stations open `http://<server-ip>:8000` (or `PORT=9000`).
- Tests: `python -m unittest discover -s tests -v` (canonical command, recorded in
  `README.md` and `TESTING.md`; `python3` fallback where `python` is absent).

## Risks / open questions

- **SQLite as the shared backend** — fine for one server process at shop scale (single
  writer, WAL, short transactions). If the shop grows to multiple server processes,
  migrate to Postgres in a Future cycle.
- **Form-based line entry UX** is deliberately minimal this cycle (no JS framework);
  scanner/voice input later replaces the same `POST /charge` payload shape.
- Tests overriding `POSHandler.db_path` remain sequential-safe (documented non-blocking
  in the baseline review).
- Realtime push, auth, and the HK tax export build on `transactions` + `receipt_no`
  next cycle — this cycle's schema is the contract for them.