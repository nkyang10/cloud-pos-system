# Tasks — Full Cloud POS System (Cycle 1: Concurrent Cloud Cashiering)

Run: `20260907-1646` — ordered, one commit per task. Tasks 1–3 by Engineer, tasks 4–5 by
QA (per the engineer brief: "leave tests for the QA role"). Each task touches a DISJOINT
file set so branches merge without conflicts.

- Implement the sale backend in `pos/store.py` only: extend `init_db` with the new tables (`receipt_sequences`, `transactions`, `transaction_lines`, `idx_txn_lines_txn`) and per-connection PRAGMAs (WAL, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`), and add `checkout(conn, lines, payment_method, payment_ref=None)` — one `with conn:` transaction that validates lines and payment method, atomically check-and-decrements stock per line (`UPDATE items SET quantity = quantity - ? WHERE id = ? AND quantity >= ?`; `ValueError` on insufficient stock / unknown item / non-positive qty / unknown method, nothing written), mints the next sequential receipt number via `receipt_sequences` (`{year}-{no:04d}`), inserts the transaction + line rows (snapshot name/price, server-side subtotal/total), and returns the sale dict — plus read helpers `get_transaction(conn, receipt_no)` and `list_transactions(conn)`.
- Add the cashiering HTTP surface in `pos/server.py` only: `GET /` renders the register page (item catalog + current-sale ticket form with `item_id`/`quantity` lines and a payment-method select), `POST /charge` validates via `store.checkout` and replies `303 See Other → /receipts/{receipt_no}` (or `400` with a clear message and no writes on `ValueError`), `GET /receipts/{receipt_no}` renders the HTML receipt (receipt number, date, itemized lines qty × unit price, subtotal, total, payment method, cash change if cash; unknown receipt → `404`), and `GET /receipts` lists past transactions; keep the existing catalog routes working.
- Turn the server into the shared cloud station endpoint in `run.py` only: bind `ThreadingHTTPServer` to `0.0.0.0` (was `127.0.0.1`) so any station on the network reaches it, keep the `PORT` env override and `_parse_port` behavior, and update the startup message to print the reachable host.
- Add QA tests for the checkout flow in `tests/test_checkout.py` (new) plus extensions to `tests/test_store.py`, `tests/test_server.py`, `tests/test_run.py` as needed: valid charge → stock decremented, unique sequential receipt number, transaction + lines persisted and visible on `GET /receipts/{no}` and `GET /receipts`; rejections (insufficient stock, unknown item, non-positive quantity, unknown payment method) leave no transaction row and no stock change; concurrency test with interleaved charges yields distinct receipt numbers and never-negative stock; existing 15 tests stay green via `python -m unittest discover -s tests`.
- QA gate + docs in `README.md` and `TESTING.md` only: record the new feature blurb, the canonical test command, and coverage/status notes; run the full suite and confirm green (stdlib runner `python -m unittest discover -s tests -v`; `python3` fallback if `python` is absent).

## Notes

Status: Engineer tasks 1–3 pending; QA gate (tasks 4–5) pending; review pending (see
`ENGINE_STATE/review.md` after this run).

File-ownership map (disjoint by design): `pos/store.py` → task 1; `pos/server.py` →
task 2; `run.py` → task 3; `tests/**` → task 4; `README.md` + `TESTING.md` → task 5.