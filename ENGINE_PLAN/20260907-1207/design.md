# Design — Stock Items List + Add Item

- Run: `20260907-1207`

## Tech stack

- **Python 3.8+ — standard library only.** `http.server` (`ThreadingHTTPServer` +
  `BaseHTTPRequestHandler`) for HTTP, `sqlite3` for persistence, `urllib.parse` for form
  parsing, `decimal.Decimal` for money parsing. Zero third-party dependencies: no `pip install`,
  runs anywhere Python exists.
- **Why:** the repo has no app framework or package files; the team brief mandates the minimal,
  dependency-light, locally-runnable option. Python stdlib satisfies that with no install step.
- **Tests:** stdlib `unittest` (lightest standard runner, per QA brief).

## Assumptions (stated, not blocking)

- "cloud-pos-system" = a small web app served on localhost; no real cloud hosting this cycle.
- Single store, single user, no auth.
- Money is stored as integer **cents** to avoid float rounding (display layer formats `$x.yz`);
  input is parsed via `Decimal` and rejected if it has more than two decimal places.
- Duplicate item names are rejected case-insensitively — a POS catalog must not have ambiguous
  items (`UNIQUE COLLATE NOCASE` is the source of truth).
- Server-rendered single HTML page; inline CSS only; no JS framework.
- Port defaults to `8000`; overridable via `PORT` env var in case it is taken (invalid values
  abort with a clear message).
- Tests are owned by the **QA role** per the engineer brief ("leave tests for the QA role") —
  the engineer implements tasks 1–3.

## File/module layout

```
run.py                    # entry point: python run.py  ->  http://127.0.0.1:8000 (PORT override)
pos/
  __init__.py             # empty package marker
  store.py                # SQLite repository: init_db(), list_items(), add_item()
  server.py               # http.server handler: GET / (list + form), POST /items (add)
stock.db                  # created at runtime (gitignored)
tests/
  test_store.py           # unit tests for store validation + persistence
  test_server.py          # end-to-end: GET / and POST /items against the real handler
  test_run.py             # run.py port resolution (AC1)
TESTING.md                # canonical test command + coverage notes
```

## Data model

Table `items` (created by `store.init_db()` if missing):

| column       | type    | constraints                              |
|--------------|---------|------------------------------------------|
| id           | INTEGER | PRIMARY KEY AUTOINCREMENT                |
| name         | TEXT    | NOT NULL, UNIQUE (COLLATE NOCASE)        |
| price_cents  | INTEGER | NOT NULL, CHECK (price_cents >= 0)       |
| quantity     | INTEGER | NOT NULL DEFAULT 0, CHECK (quantity >= 0)|

No other tables this cycle.

## Key APIs

`pos/store.py`:
- `init_db(db_path="stock.db") -> sqlite3.Connection` — opens (creating if needed) the database,
  ensures the schema, sets `row_factory=sqlite3.Row`, commits; returns the connection.
- `list_items(conn) -> list[dict]` — rows `{id, name, price_cents, quantity}` ordered by `id`
  (insertion order).
- `add_item(conn, name, price_cents, quantity=0) -> int` — validates, inserts, returns the new
  id; raises `ValueError` on validation failure. Caller is responsible for commit/rollback.

`pos/server.py`:
- `POSHandler(BaseHTTPRequestHandler)` with class attribute `db_path = "stock.db"` (tests
  override it).
- `GET /` → `200` HTML: items table + add-item form (fields `name`, `price`, `quantity`);
  empty-state message when there are no rows; any other path → `404`.
- `POST /items` → parse `application/x-www-form-urlencoded` body; on success insert, commit, and
  reply `303 See Other → /`; on validation error reply `400` HTML with a clear message and a link
  back; any other path → `404`.
- Helpers: `format_price(cents)`, `parse_price_to_cents(raw)`, `parse_quantity(raw)`.

`run.py`:
- `main()` — binds `ThreadingHTTPServer` on `127.0.0.1:PORT` (default 8000) and serves forever.
- `_parse_port(raw)` — parses `PORT` env var; blank/missing → 8000; non-integer or out-of-range
  (1–65535) → `SystemExit` with a clear message.

Validation rules (in `store.add_item`, mirrored by the form and server parsers):
- `name`: trimmed, non-empty, unique (case-insensitive).
- `price`: decimal like `"3.50"` parsed to cents (`350`); must be >= 0, finite, at most two
  decimals.
- `quantity`: integer >= 0; blank/missing defaults to 0.

## Running & testing

- Run: `python run.py` → open `http://127.0.0.1:8000` (or `PORT=9000 python run.py`).
- Tests: `python -m unittest discover -s tests -v` (canonical command, recorded in
  `README.md` and `TESTING.md`).

## Risks / open questions

- Port 8000 may be busy — handled via `PORT` env var.
- Float-precision on price — avoided by integer cents + `Decimal` parsing.
- Tests override the class attribute `POSHandler.db_path` and restore it in `tearDown`; safe for
  the sequential stdlib runner, but fragile under parallel execution (known, non-blocking).