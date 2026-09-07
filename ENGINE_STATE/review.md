# Review — run `20260907-1207` (Stock Items List + Add Item)

verdict: APPROVE

## Scope reviewed

Diff of `engine/20260907-1207` vs base `main` (empty repo at `4a453e2`), assessed against
`ENGINE_PLAN/20260907-1207/{PRD,design,tasks}.md`. Feature files: `run.py`, `pos/store.py`,
`pos/server.py`, `pos/__init__.py`, `tests/test_store.py`, `tests/test_server.py`,
`tests/test_run.py`, `README.md`, `TESTING.md`, `.gitignore`. The remaining files in the
branch diff (`.opencode/agent/*.md`, `ENGINE_PLAN/**`) are engine-run orchestration artifacts,
not product code.

## Findings

1. **AC1 met — stdlib server on port 8000.** `run.py` uses only `http.server`/`os`/stdlib; binds
   `127.0.0.1:8000` with `PORT` env override (validated 1–65535, invalid values abort with a
   clear message). Verified live: `GET / -> 200`.
2. **AC2 met — list + empty state + currency.** `GET /` renders an HTML table with Name /
   Price (formatted via `format_price`, integer cents → `$x.yz`) / Quantity, ordered by id;
   a fresh DB renders "No items yet" (verified live and by test
   `test_index_shows_empty_state_when_no_items`).
3. **AC3 met — valid add.** `POST /items` with valid name/price/quantity inserts and replies
   `303 See Other -> /`; the item then appears in the list. Verified live and by
   `test_add_item_then_list_shows_it`. Duplicate-name uniqueness is case-insensitive
   (`UNIQUE COLLATE NOCASE` + pre-check), matching the design assumption.
4. **AC4 met — invalid submissions rejected.** Blank/missing name, duplicate name, negative
   price, non-numeric or >2-decimal price, negative/non-integer quantity and missing price all
   return `400` with a clear message and insert no row (`test_invalid_submissions_are_rejected_with_400`
   checks row count after every rejection; `test_invalid_input_is_rejected` does the same at the
   store level). Missing quantity defaults to 0 as declared in design.md.
5. **AC5 met — persistence.** Items stored in SQLite `stock.db` (schema per design.md: `id`,
   `name`, `price_cents` INTEGER CHECK >= 0, `quantity` INTEGER CHECK >= 0). Persistence across
   reopen is unit-tested (`test_items_persist_across_a_reopen`) and confirmed manually across a
   server restart; `stock.db` is gitignored.
6. **AC6 met — QA suite exists and is green.** Canonical stdlib command
   `python -m unittest discover -s tests` documented in both `README.md` and `TESTING.md`.
   Ran the suite: **15 tests, all passing (Ran 15 tests … OK)** on Python 3.12. Coverage:
   store validation/persistence, end-to-end handler flows (empty state, add→list, ordering,
   quantity default, 400s), and `run.py` port resolution.
7. **Diff matches declared design; no scope creep.** Layout, module APIs
   (`init_db`/`list_items`/`add_item`), routes (`GET /`, `POST /items`), data model, integer-cents
   money, case-insensitive uniqueness, and PORT override all match design.md. No edit/delete,
   checkout, auth, JSON API, or other non-goal features leaked in. QA's extra `tests/test_run.py`
   (AC1 coverage) is documented in tasks.md/TESTING.md and is consistent with the QA brief.
8. **No secrets, debug leftovers, or dead code.** Grep for password/secret/token/API-key and for
   TODO/FIXME/debugger/breakpoint found nothing in feature code. The only prints are the
   intentional startup/shutdown messages in `run.py`. All helpers (`parse_price_to_cents`,
   `parse_quantity`, `format_price`, `_page`, `_add_form`, …) are exercised by tests. User-supplied
   `name` is HTML-escaped before rendering (no stored-XSS vector); `quantity`/price are DB-typed
   integers.

## Non-blocking observations (no change required)

- `run.py` binds `127.0.0.1` only, not `0.0.0.0` — matches the design's localhost intent.
- `BaseHTTPRequestHandler` writes one access-log line per request to stderr; normal stdlib
  behavior, not a defect.
- Tests override the class attribute `POSHandler.db_path` and restore it in `tearDown`; safe for
  this sequential stdlib runner, but it would be fragile under parallel execution.

## Positives

- Clean separation: store (validation + SQL) vs handler (HTTP + form parsing), with the caller
  owning commit/rollback as designed.
- Price parsing via `Decimal` and integer cents avoids float issues and enforces the
  two-decimal rule.
- Empty-state UX, `303` post-redirect-get, clear `400` error pages with a back link — all match
  the brief.
- Tests assert "no row inserted" after each rejection, not just the status code.

**Overall:** All six acceptance criteria are satisfied, the diff faithfully implements the
declared design with no scope creep, the QA suite (15 tests) is present, green, and its canonical
command is documented. Approve.
