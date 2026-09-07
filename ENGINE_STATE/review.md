# Review — run `20260907-1646` (Stock Quantity Adjustments, In/Out)

verdict: APPROVE

## Scope reviewed

Diff of the feature branch `engine/20260907-1646` vs its plan base
(`git diff 854adf6~1..HEAD`), which isolates this cycle's changes:
`pos/store.py`, `pos/server.py`, `tests/test_movements.py`,
`tests/test_adjust_http.py`, `README.md`, `TESTING.md`, `.gitignore`,
`ENGINE_STATE/QA-20260907-1646.md`, plus the plan docs. `run.py` and the
existing tests (`test_store.py`, `test_server.py`, `test_run.py`) are
unchanged, confirming no regression surface was touched.

## Positives

- **Correct against PRD.** All 8 acceptance criteria are met and each is
  exercised by a test:
  - AC1: `init_db` applies `journal_mode=WAL`, `foreign_keys=ON`,
    `busy_timeout=5000`, `synchronous=NORMAL` before the DDL block; the
    pre-existing suite stays green.
  - AC2: `stock_movements` table (id, item_id FK, `delta` CHECK != 0,
    `movement_type` CHECK IN ('in','out'), `created_at` default now) + index
    on `item_id`, created idempotently via additive `CREATE TABLE/INDEX IF NOT
    EXISTS`.
  - AC3: `adjust_quantity` validates movement_type → non-zero-int (bool
    excluded) → sign-vs-type → item exists (`ItemNotFoundError`, a
    `ValueError` subclass) → non-negative on-hand, then inserts the row and
    updates quantity inside one `with conn:` transaction, returning the new
    on-hand. `list_movements` implemented as the audit helper.
  - AC4: rejections (net-negative, sign mismatch, bad delta/type, unknown
    item) record no movement row and leave `items.quantity` unchanged — tested.
  - AC5: `quantity == SUM(delta)` invariant tested after in/out sequences.
  - AC6: `POST /items/{id}/adjust` → 303 on success, 400 on invalid/net-negative,
    404 on unknown item/path; `GET /` renders a per-row Adjust form; all user
    input HTML-escaped.
  - AC7: persistence across reopen tested at both store and HTTP layers.
  - AC8: full QA suite (34 tests) green; canonical command documented in
    `TESTING.md` and `README.md`.
- **Matches the declared design, no scope creep.** Every change maps to the
  design/tasks; checkout, auth, ledger UI, edit/delete are correctly left out.
- **Transaction ownership is sound.** Store manages its own `with conn:`;
  `_add_item`'s manual commit/rollback was correctly replaced with `with conn:`
  (a declared refinement) and the existing add tests still pass.
- **Test suite is real and green.** Independently ran
  `python3 -m unittest discover -s tests -v` → 34 tests, OK. The count matches
  the documented number. Existing tests were not weakened/deleted.
- **Cleanliness.** No debug prints, TODOs, or secrets in `pos/`; the QA cleanup
  commit that removed duplicate `_PRAGMAS`/`ItemNotFoundError` from the merge
  resolution is verified (final `pos/store.py` has no duplicates).
- **Docs are honest.** `TESTING.md` documents the command, coverage, status, and
  the environment note (`python3` vs `python`); `README.md` feature blurb updated.

## Findings (non-blocking)

1. `_adjust_item` imports `ItemNotFoundError, adjust_quantity` inside the method
   body rather than at module top. It is explained by a comment (allows the
   server module to import before the store merge) and is functionally correct
   now that the store provides these symbols. Cosmetic only; could be hoisted to
   a top-level import in a future cycle.
2. The local SQLite engine's busy_timeout is set to 5000ms but the store's
   net-negative check reads the current quantity then writes in a separate
   `with conn:` block, so two concurrent writers could in principle both pass
   the pre-check. The design explicitly scopes this out for this cycle (single
   user on localhost, WAL + busy_timeout as the precondition) and lists
   multi-station sync as a TARGETS.md future item. Not a defect against ACs.

## Conclusion

The feature is correctly implemented against the PRD, matches the declared
design with no unrelated changes, has a real and documented QA suite that is
green (34 tests), and contains no secrets, debug leftovers, dead code, or
broken tests. Approve.
