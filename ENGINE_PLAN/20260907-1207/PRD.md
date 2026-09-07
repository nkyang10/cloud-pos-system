# PRD — Stock Items List + Add Item

- Run: `20260907-1207`
- Epic source: [nkyang10/cloud-pos-system#12](https://github.com/nkyang10/cloud-pos-system/issues/12) — "A tiny POS system: track stock items"

## Problem

This repo is a fresh playground for a tiny POS (point-of-sale) system. There is currently no
application code — only a README and agent configuration. The first shippable slice is the
stock-tracking foundation: the cashier can see all stock items and add a new one. This catalog is
the base that later cycles (checkout, stock adjustments) will build on.

## MUST-HAVE happy path

1. User starts the app locally: `python run.py`.
2. User opens `http://localhost:8000` in a browser.
3. The page shows the list of stock items (name, price, quantity on hand). An empty database
   shows a clear "no items yet" message instead of an error.
4. User fills the "Add item" form (name, price, quantity) and submits.
5. The new item appears in the list immediately, appended after existing items.
6. Items persist across a server restart (stored in a SQLite file).

## Non-goals (this cycle)

- No authentication, user accounts, or multi-store/tenant support.
- No edit, delete, or stock-adjustment (increment/decrement) actions.
- No sale/checkout flow, receipts, or payments.
- No search, filter, pagination, categories, barcode, or unit fields.
- No JSON REST API, no front-end framework, no Docker/CI/deployment.

## Acceptance criteria

- **AC1** — `python run.py` starts a local server on port 8000 using only the Python standard
  library; `GET /` returns HTML listing stock items.
- **AC2** — The list shows every item's name, price (formatted as currency), and quantity; an
  empty database renders an empty-state message.
- **AC3** — Submitting the add form with a valid name (non-blank, unique), price (>= 0), and
  quantity (>= 0 integer) creates the item, and the list shows it on the next view.
- **AC4** — Invalid submissions (blank or duplicate name, negative price, negative or non-integer
  quantity, missing fields) are rejected with a clear error message and no row is inserted.
- **AC5** — Items survive a server restart (SQLite persistence in `stock.db`).
- **AC6** — Tests run with the stdlib runner `python -m unittest discover -s tests` and all pass.

## Future (out of scope)

Edit and delete items, quantity adjustments, checkout flow, categories, JSON API, real deployment.