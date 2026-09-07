# NEXT-CYCLE — Basic Cashiering: Sale Flow + HTML Receipt

- Run: `20260907-1646` (proposal for the *next* cycle)
- Current state: **stock quantity adjustments (in/out) on an append-only movement ledger**
  shipped and APPROVED (`ENGINE_STATE/review.md`; all 8 ACs met; 34 tests green).
  No blocking issues; 2 non-blocking findings (below).
- Target of next cycle: the PRD's first "Future" item — **checkout/sale flow** (a sale =
  a stock-`out` movement plus unit-price snapshotting) — the prior NEXT-CYCLE deferred it
  "until the ledger + adjust are stable", which they now are. This is the first slice of the
  user requirement's **basic cashiering (sale/receipt/refund)** and of the
  **HTML report export for Hong Kong tax** (the receipt is the IRD-friendly report core).
  Grounded in `ENGINE_RESEARCH.md` §5 (single-writer receipt sequences, atomic stock
  decrement) and §8 (receipt field checklist).

## Refinements (small, to the just-shipped code)

1. **Hoist the in-method import in `pos/server._adjust_item`** to the module top
   (`from pos.store import ItemNotFoundError, adjust_quantity`). The store now provides
   these symbols at import time, so the comment explaining the late import is obsolete.
   Verify: full suite stays 34 green. (Review finding 1.)

2. **Make the net-negative check in `pos.store.adjust_quantity` atomic.** Today it reads
   `quantity` in a separate `SELECT` and then writes inside `with conn:`, so two concurrent
   writers could both pass the pre-check (review finding 2 — scoped out last cycle). Replace
   with the atomic conditional update `UPDATE items SET quantity = quantity + ? WHERE id = ? AND quantity + ? >= 0`
   (item-exists check first; 0 rows → `ValueError`, no movement row), keeping the movement
   insert in the same `with conn:` transaction; read the new quantity back for the return
   value. Verify: existing 34 tests stay green. Closes the race before the sale flow adds a
   second stock-decrement write path.

## New tasks (next cycle, ordered by value)

1. `[research]` **Store layer — `create_sale`:** add `sales` (`id`, `receipt_no` UNIQUE,
   `subtotal_cents`, `total_cents`, `payment_method`, `payment_ref`, `created_at`),
   `sale_lines` (`sale_id` FK, `item_id` FK, `qty`, `unit_price_cents` snapshot,
   `line_total_cents`) and a single-writer `receipt_sequences` table (year, type, last_no);
   `create_sale(conn, lines, payment_method)` mints the next `receipt_no` (atomic counter
   increment inside the transaction) and, in ONE `with conn:` block, inserts the sale +
   lines, one `out` movement per line, and decrements stock via conditional
   `UPDATE items SET quantity = quantity - ? WHERE id = ? AND quantity >= ?` (0 rows →
   `ValueError`, nothing recorded). (Research §5: single-writer receipt numbers, atomic
   stock decrement; PRD "sale = `out` movement, unit-price snapshotting".)

2. `[research]` **HTTP surface — the sale form:** add `GET /sale` (per-row item qty inputs
   + payment-method select — cash/card/octopus/fps-qr/alipayhk/wechatpayhk/payme — plus
   optional reference field, and a link/button from `GET /`) and `POST /sale` → `303 See
   Other → /receipts/{receipt_no}` on success, `400` with a clear message on invalid input
   or insufficient stock, `404` on unknown item/path. (Research §5 stock-decrement errors,
   §8 payment rails.)

3. `[research]` **Receipt rendering — `GET /receipts/{receipt_no}`:** HTML receipt with the
   IRD-audit-friendly core per research §8 — sequential receipt number, date/time,
   itemized lines (description, qty, unit price, amount), subtotal, total, payment method +
   ref — print-friendly layout, all values HTML-escaped; unknown receipt_no → 404. This is
   the first shippable slice of "HTML report export for Hong Kong tax". (Research §8 field
   checklist; BRN/store-name config deliberately deferred.)

4. **QA gate:** add `tests/test_sales.py` (receipt_no increments across sequential sales;
   atomicity — insufficient stock records no sale/movements/quantity change; unit-price
   snapshot survives a later price edit; `quantity == SUM(delta)` invariant still holds
   after a sale) and `tests/test_sale_http.py` (`POST /sale` → 303 → `GET /receipts/N`
   shows itemized receipt; insufficient stock → 400; unknown item → 404; persistence across
   a reopen); update `TESTING.md`; run the full suite green and record status.

## Explicitly deferred (do NOT do this cycle)

- Refund flow (needs receipt lookup + reversal with negative totals — next-next cycle).
- Coupons, auth, realtime multi-station sync, BRN/store-config settings, ledger-history UI.
- Card/Octopus payment *processing* (recording only, per research §14 open question 5).

## Traceability

- Refinement 1 → review finding 1. Refinement 2 → review finding 2 + research §5.
- Task 1 → PRD Future "checkout/sale flow" + research §5 (receipt sequence, atomic stock).
- Task 2 → user requirement "basic cashiering" + research §5/§8 (payment-method registry).
- Task 3 → user requirement "HTML report export for Hong Kong tax" + research §8.
- Task 4 → QA brief + research §5 invariants (audit, atomicity, snapshot).