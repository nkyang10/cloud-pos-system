# Tasks — Stock Items List + Add Item

Run: `20260907-1207` — ordered, one commit per task, tasks 1–3 by Engineer, task 4 by QA (per engineer brief).

- Create `pos/store.py` with SQLite schema init and `list_items()` / `add_item()` including validation (blank/duplicate name, non-negative cents price, non-negative integer quantity).
- Add `pos/server.py` with `GET /` rendering the items table plus add-item form and `POST /items` validating, inserting, and redirecting to `/` on success / returning 400 with a clear message on failure.
- Add `run.py` entry point (port from `PORT` env, default 8000) and update README with run and test commands.
- Add happy-path tests in `tests/test_store.py` and `tests/test_server.py` (empty list → add → list shows item; invalid input rejected) runnable via `python -m unittest discover -s tests`.

## QA gate (task 4) — DONE

- Verified the existing suite (10 tests) is green.
- Added `tests/test_run.py` (port resolution, PRD AC1) and extended
  `tests/test_server.py` (missing-name 400, >2-decimal price 400, omitted-quantity
  defaults to 0). Suite is now 15 tests, all passing.
- Manually booted the server and confirmed `GET / -> 200`, `POST /items -> 303`,
  and the new item appears in the list (full happy path).
- Canonical test command (also in README + TESTING.md):

      python -m unittest discover -s tests -v
