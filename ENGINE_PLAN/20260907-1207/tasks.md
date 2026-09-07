# Tasks — Stock Items List + Add Item

Run: `20260907-1207` — ordered, one commit per task. Tasks 1–3 by Engineer, tasks 4–5 by QA
(per the engineer brief: "leave tests for the QA role").

- Create `pos/store.py` with SQLite schema init (`items` table, `UNIQUE COLLATE NOCASE` name, integer-cents price) and `init_db()` / `list_items()` / `add_item()` including validation (blank/duplicate case-insensitive name, non-negative cents price, non-negative integer quantity).
- Add `pos/server.py` serving `GET /` (items table + empty-state message + add-item form) and `POST /items` (parse + validate + insert → `303` to `/`; `400` with a clear message on invalid input; `404` for any other path).
- Add `run.py` entry point (port from `PORT` env, default 8000, invalid values abort with a clear message) and update README with run and test commands.
- Add happy-path tests in `tests/test_store.py` and `tests/test_server.py` (empty list → add → list shows item; invalid input rejected with no row inserted), runnable via `python -m unittest discover -s tests`.
- QA gate: add `tests/test_run.py` (port resolution, PRD AC1) and `TESTING.md` with the canonical test command, then run the full suite and confirm green.

## Status

- Engineer tasks 1–3: DONE (implemented and merged).
- QA gate (tasks 4–5): DONE — suite extended to 15 tests and verified green on Python 3.12
  (`python -m unittest discover -s tests -v` → "Ran 15 tests … OK"); happy path verified live
  (`GET /` → 200, `POST /items` → 303, item appears in the list).
- Review verdict: APPROVE (see `ENGINE_STATE/review.md`).