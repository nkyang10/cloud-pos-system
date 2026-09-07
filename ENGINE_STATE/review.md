# Review — run `20260907-1207` (Stock Items List + Add Item)

verdict: APPROVE

## Scope reviewed

Diff of branch `engine/20260907-1207` (HEAD `e2c2f4c`) vs base `main`
(`4a453e2`, empty repo), assessed against
`ENGINE_PLAN/20260907-1207/{PRD,design,tasks}.md`. Product/feature files:
`run.py`, `pos/{__init__,store,server}.py`, `tests/{test_store,test_server,test_run}.py`,
`README.md`, `TESTING.md`, `.gitignore`. The rest of the branch diff
(`.opencode/agent/*.md`, `ENGINE_PLAN/**`, `ENGINE_STATE/**`) is engine-run
orchestration artifacts, not product code.

Verification was done independently this session: full test run (15 passing),
live smoke test of the running server, and a secret/TODO/debug scan of the
feature code.

## Findings

1. **AC1 met — stdlib server, port 8000, PORT override.** `run.py` uses only
   `http.server`/`os` stdlib, binds `127.0.0.1:8000`, and overrides via `PORT`
   (validated integer 1–65535; invalid values abort with a clear
   `SystemExit`). Verified live: `PORT=8123 python3 run.py` served `GET /` →
   200.
2. **AC2 met — list + empty state + currency.** `GET /` renders an HTML table
   (Name / Price via `format_price` cents→`$x.yz` / Quantity) ordered by id; a
   fresh DB shows "No items yet" (`test_index_shows_empty_state_when_no_items`,
   confirmed live).
3. **AC3 met — valid add.** `POST /items` validates and inserts, then replies
   `303 See Other → /`; the item appears on the next view with formatted price
   and quantity. Verified live and by `test_add_item_then_list_shows_it`.
   Duplicate-name uniqueness is case-insensitive (`UNIQUE COLLATE NOCASE` +
   pre-check), per design.
4. **AC4 met — invalid submissions rejected, no row inserted.** Blank/missing
   name, duplicate (case-insensitive), negative/non-numeric/>2-decimal price,
   negative/non-integer quantity all return `400` with a clear message and no
   insert. `test_invalid_submissions_are_rejected_with_400` asserts the row
   count is unchanged after every rejection (confirmed live: duplicate →
   400).
5. **AC5 met — persistence.** Items persist in SQLite `stock.db` (schema per
   design: `id`, `name`, `price_cents` INTEGER CHECK>=0, `quantity` INTEGER
   CHECK>=0); `test_items_persist_across_a_reopen` covers reopen. `stock.db` is
   gitignored.
6. **AC6 met — QA suite exists and is green; command documented.** Canonical
   stdlib command `python -m unittest discover -s tests` is documented in both
   `README.md` and `TESTING.md` (with a `python3` fallback). Ran the suite this
   session: **Ran 15 tests … OK** (Python 3.12). Coverage spans store
   validation/persistence, end-to-end handler flows (empty state, add→list,
   ordering, quantity default, 400s), and `run.py` port resolution.
7. **Diff matches declared design; no scope creep.** Layout, module APIs
   (`init_db`/`list_items`/`add_item`), routes (`GET /`, `POST /items`, 404 for
   others), integer-cents money via `Decimal`, case-insensitive uniqueness, and
   PORT override all match `design.md`. No edit/delete, checkout, auth, JSON
   API, or other non-goal leaked in. QA's extra `tests/test_run.py` (AC1
   coverage) is consistent with the QA brief and documented.
8. **No secrets, debug leftovers, dead code, or broken tests.** Feature code
   has no password/secret/token/API-key strings and no TODO/FIXME/breakpoint/
   pdb. The only prints are intentional startup/shutdown messages in `run.py`
   and the stdlib per-request access log. All helpers are exercised by tests.
   User-supplied `name` is HTML-escaped before rendering; `quantity`/price are
   DB-typed integers.

## Non-blocking observations (no change required)

- `run.py` binds `127.0.0.1` only (not `0.0.0.0`) — matches the design's
  localhost intent.
- Tests override the class attribute `POSHandler.db_path` and restore it in
  `tearDown`; safe for the sequential stdlib runner, but fragile under parallel
  execution (documented in design.md, known and non-blocking).

## Positives

- Clean store (validation + SQL) vs handler (HTTP + form parsing) separation,
  with the caller owning commit/rollback as designed.
- `Decimal` + integer cents avoids float issues and enforces the two-decimal
  rule; blank quantity defaults to 0 as declared.
- Empty-state UX, `303` post-redirect-get, and clear `400` error pages with a
  back link all match the brief.
- Tests assert "no row inserted" after each rejection, not just the status code.

**Overall:** All six acceptance criteria are satisfied, the diff faithfully
implements the declared design with no scope creep, the QA suite (15 tests) is
present, green, and its canonical command is documented. Approve.
