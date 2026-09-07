# Testing

## Runner

The project uses the Python standard-library `unittest` runner (no third-party
dependencies, no `pip install` needed).

Run the full suite from the repo root:

    python -m unittest discover -s tests -v

If `python` is not on your PATH (some distros only ship `python3`), use:

    python3 -m unittest discover -s tests -v

Either is the canonical test command for the driver / CI to re-run.

## What is covered

- `tests/test_store.py` — unit tests for the SQLite store: empty list, add →
  list shows item, default quantity, insertion order, validation rejection
  (blank/duplicate name, negative price, negative/non-integer quantity), and
  persistence across a reopen.
- `tests/test_server.py` — end-to-end tests against the real `POSHandler`:
  empty-state page, add → redirect (303) → item listed with formatted price and
  quantity, insertion order, omission-of-quantity defaulting to 0, and 400
  rejection (blank/missing name, duplicate, negative/non-numeric/>2-decimal
  price, negative/non-integer quantity) with no row inserted.
- `tests/test_run.py` — `run.py` port resolution (PRD AC1): default 8000, valid
  `PORT` override, and rejection of non-numeric / out-of-range ports.
- `tests/test_movements.py` — store-level tests for the stock-movement ledger
  and `adjust_quantity`: in/out happy path (movement row inserted, returned and
  stored new quantity, `list_movements` ordering and item filter), the
  `quantity == SUM(delta)` invariant (AC5), rejections (unknown item →
  `ItemNotFoundError`, net-negative → `ValueError` with no row and unchanged
  quantity, sign/type mismatch, zero/non-int delta, bad `movement_type`), and
  persistence across a reopen.
- `tests/test_adjust_http.py` — end-to-end tests of `POST /items/{id}/adjust`
  through the real handler: valid in/out → `303` → updated quantity on `GET /`
  (and the per-row Adjust form), net-negative → `400` with quantity unchanged
  and no movement row, unknown item id → `404`, bad/missing `delta` or
  `movement_type` → `400`, unknown paths → `404`, and persistence across a
  reopen.

## Status

The suite is green. Verified with Python 3.12 (`python3` — `python` is not on
PATH in this environment; either invocation works):

    python3 -m unittest discover -s tests -v
    Ran 34 tests in 0.124s
    OK

Live smoke check also confirmed: `GET /` → 200, `POST /items` (valid) → 303 →
item listed with formatted price/quantity.
