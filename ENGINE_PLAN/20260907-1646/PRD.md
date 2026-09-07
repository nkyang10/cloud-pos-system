# PRD — Full Cloud POS System (Cycle 1: Concurrent Cloud Cashiering)

- Run: `20260907-1646`
- Target feature: **full cloud POS system**
- Approved input: `ENGINE_PLAN/20260907-1646/NEXT-CYCLE.md` does **not** exist on this
  branch; the target feature itself (per `ENGINE_STATE/TARGETS.md`) is the approved input.
  (`ENGINE_PLAN/20260907-1207/NEXT-CYCLE.md` exists but proposes *stock quantity
  adjustments* for the 1207 cycle's next step; the target for THIS cycle is the full cloud
  POS system, so that proposal is noted and deferred.)
- Baseline: run `20260907-1207` shipped and was APPROVED (`ENGINE_STATE/review.md`) — a
  stdlib-only stock catalog: list items + add item (`pos/store.py`, `pos/server.py`,
  `run.py`), server bound to `127.0.0.1`, 15 tests green (verified this session).

## Problem

The repo today is a single-user, localhost stock catalog: list items and add items. The
target is a **full cloud POS system** (per `ENGINE_STATE/TARGETS.md`): server + database
on the cloud side, HTML client with auth, barcode/QR scanner input, a voice AI cashier
assistant, basic cashiering (sale/receipt/refund), customizable coupons, HTML export for
Hong Kong tax, multi-station concurrency against one backend, item CRUD persisted to the
cloud, and realtime sync to every online station.

That end-state is far too large for one cycle. The **foundation every other requirement
rests on is the concurrent sale (cashiering) flow**: multiple stations charging sales
against one shared backend without corrupting data (receipt numbering, stock decrement).
Until that works, coupons, refunds, voice, scanners, and the HK tax report have nothing
to operate on. This cycle therefore ships the **core cloud cashiering flow** on the
existing stdlib/SQLite stack, extended into a shared, concurrency-safe, multi-station
server — the first shippable increment of the full cloud POS system.

## MUST-HAVE happy path

1. One shared server process is started (`python run.py`), serving the POS for the whole
   shop; any station's browser reaches it over the network (bind `0.0.0.0`, port `PORT`).
2. A cashier opens the register page: the item catalog is shown, and a **current sale**
   ticket (lines, quantities, running total) is built on the page by picking items.
3. The cashier charges the sale and picks a payment method (cash / card / octopus / fps /
   alipayhk / wechatpayhk / payme / store-credit).
4. The server records the sale **atomically**: mints a unique sequential receipt number,
   decrements stock only if sufficient (never negative), and persists the transaction
   with its line items in SQLite — safe when two stations charge concurrently.
5. The cashier is redirected to the **HTML receipt** for that sale (receipt number, date,
   store, itemized lines with qty × unit price, subtotal/total, payment method, change if
   cash) — the seed of the future HK tax receipt export.
6. A `GET /receipts` page lists past sales (receipt number, date, total) for audit and
   the later refund flow.

## Non-goals (this cycle)

- No authentication / user accounts / RBAC (the shared server is trusted on the shop LAN;
  auth is a Future cycle).
- No realtime push to stations, no camera QR scanning, no keyboard-wedge barcode
  capture, no voice assistant.
- No refund / void flow, no coupons / discounts, no item edit/delete, no stock in/out
  adjustments UI (deferred per the 1207 NEXT-CYCLE proposal — checkout's atomic stock
  decrement already establishes the ledger's core invariant).
- No deployment, Docker, Postgres, or Redis migration — the SQLite backend remains the
  single source of truth and is made concurrency-safe this cycle.
- No thermal printing, no payment *processing* integration (payments are recorded by
  method + optional reference only).

## Acceptance criteria

- **AC1** — `python run.py` starts the server bound to `0.0.0.0` (port `PORT`, default
  `8000`) using only the Python standard library, so multiple stations can reach it.
- **AC2** — `GET /` renders the register page: the item catalog plus a current-sale
  ticket (item + quantity lines, running total) and the payment-method choices.
- **AC3** — Charging a sale (`POST /charge`) with valid lines and a valid payment method
  succeeds: stock is decremented per line, a **unique sequential receipt number** is
  minted, the transaction + line items persist, and the response is
  `303 See Other → /receipts/{receipt_no}`.
- **AC4** — Insufficient stock, unknown item id, non-positive quantity, or an unknown
  payment method is rejected with a clear `400` message and **no transaction row and no
  stock change** occur.
- **AC5** — `GET /receipts/{receipt_no}` renders an HTML receipt (receipt number, date,
  itemized lines qty × unit price, subtotal, total, payment method, cash change when
  cash); `GET /receipts` lists all past transactions. Unknown receipt → `404`.
- **AC6** — Concurrency safety is verified by test: interleaved charges from two
  connections produce distinct sequential receipt numbers and never negative stock
  (each sale checks `quantity >= qty` inside its transaction).
- **AC7** — Tests run with the stdlib runner `python -m unittest discover -s tests` and
  all pass (existing 15 tests stay green + new checkout coverage).

## Future (out of scope)

Auth/RBAC, realtime multi-station sync, barcode/QR scanner input, voice AI assistant,
refund/void, coupons/discounts, item edit/delete + stock in/out adjustments UI, full HK
tax report export, Postgres/Redis/deployment, offline stations, professional UI flow.