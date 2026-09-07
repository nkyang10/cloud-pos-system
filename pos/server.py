"""POS catalog + cashiering HTTP endpoints (stdlib only).

Routes
------
GET  /                -> 200 HTML register page: the item catalog, the
                         add-item form, and the current-sale ticket
                         (``item_id``/``quantity`` line rows + a
                         payment-method select).
POST /items           -> parse the ``application/x-www-form-urlencoded`` body,
                         validate, insert via ``pos.store``, then reply
                         303 See Other -> "/" on success, or 400 HTML with a
                         clear message on failure.
POST /charge          -> parse the urlencoded ticket, validate + record the
                         sale atomically via ``pos.store.checkout``, then reply
                         303 See Other -> "/receipts/{receipt_no}" on success,
                         or 400 HTML with a clear message on failure (nothing
                         is written).
GET  /receipts        -> 200 HTML list of past transactions (newest first).
GET  /receipts/{no}   -> 200 HTML receipt for one sale; 404 when unknown.
"""

import html
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from pos.store import (
    PAYMENT_METHODS,
    add_item,
    checkout,
    get_transaction,
    init_db,
    list_items,
    list_transactions,
)

PAGE_STYLE = """
  body { font-family: system-ui, -apple-system, sans-serif; max-width: 640px;
         margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { border-bottom: 2px solid #e5e5e5; padding-bottom: .3rem; }
  table { border-collapse: collapse; width: 100%; margin: 1.25rem 0; }
  th, td { text-align: left; padding: .5rem .75rem;
           border-bottom: 1px solid #ececec; }
  th { background: #f6f6f6; }
  .empty { color: #777; font-style: italic; }
  .error { background: #fdecea; color: #8a1f11; border: 1px solid #f5c6cb;
           border-radius: 4px; padding: .6rem .8rem; }
  form { display: grid; gap: .8rem; max-width: 18rem; margin-top: 1rem; }
  label { display: grid; gap: .25rem; font-weight: 600; font-size: .95rem; }
  input, select { padding: .4rem .5rem; font-size: 1rem;
                  border: 1px solid #bbb; border-radius: 4px; background: #fff; }
  button { justify-self: start; padding: .5rem 1.25rem; font-size: 1rem;
           cursor: pointer; }
  a { color: #06c; }
  form.ticket { max-width: 28rem; border: 1px solid #e5e5e5;
                border-radius: 6px; padding: 1rem; }
  .ticket-line { display: flex; gap: .5rem; align-items: center; }
  .ticket-line select { flex: 1 1 auto; min-width: 0; }
  .ticket-line input[type="number"] { width: 4.5rem; }
  .ticket .actions { display: grid; gap: .8rem; }
  .muted { color: #777; font-size: .9rem; }
  .receipt { border: 1px solid #e5e5e5; border-radius: 6px;
             padding: 1rem 1.5rem; max-width: 26rem; margin: 1.25rem 0; }
  .receipt h2 { border: none; }
  .receipt .meta { color: #777; font-size: .9rem; }
  .totals { margin-top: .5rem; }
"""

#: Human-readable labels for the payment methods defined in ``pos.store``.
PAYMENT_METHOD_LABELS = {
    "cash": "Cash",
    "card": "Card",
    "octopus": "Octopus",
    "fps": "FPS",
    "alipayhk": "AlipayHK",
    "wechatpayhk": "WeChat Pay HK",
    "payme": "PayMe",
    "store-credit": "Store Credit",
}

#: Number of blank item/quantity line rows on the register ticket form.
TICKET_LINE_ROWS = 5


def format_price(cents):
    """Render integer cents as dollars, e.g. 350 -> '$3.50'."""
    return "${}.{:02d}".format(cents // 100, cents % 100)


def parse_price_to_cents(raw):
    """Parse a form price like ``"3.50"`` into integer cents.

    Blank, non-numeric, negative or >2-decimal values raise ``ValueError``
    with a user-facing message. Mirrors the validation documented in the
    design (price stored as integer cents).
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Price is required.")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError("Price must be a number like 3.50, got {!r}.".format(text)) from None
    if not amount.is_finite():
        raise ValueError("Price must be a finite number.")
    if amount < 0:
        raise ValueError("Price must be zero or greater.")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError("Price can have at most two decimal places.")
    return int(cents)


def parse_quantity(raw):
    """Parse a form quantity into a non-negative int; blank defaults to 0.

    Raises ``ValueError`` with a user-facing message for non-integer or
    negative input.
    """
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        value = int(text)
    except ValueError:
        raise ValueError("Quantity must be a whole number, got {!r}.".format(text)) from None
    if value < 0:
        raise ValueError("Quantity must be zero or greater.")
    return value


def _page(title, content):
    """Wrap ``content`` in a minimal HTML document."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>{}</title>\n"
        "<style>{}</style>\n"
        "</head>\n"
        "<body>\n"
        "{}\n"
        "</body>\n"
        "</html>\n"
    ).format(html.escape(title), PAGE_STYLE, content)


class POSHandler(BaseHTTPRequestHandler):
    """Serves the POS register page, add-item form and cashiering routes."""

    # SQLite file the handler reads/writes; tests can override this attribute.
    db_path = "stock.db"

    # ------------------------------------------------------------- HTTP verbs

    def do_GET(self):
        path = _path(self.path)
        if path == "/":
            self._show_register()
        elif path == "/receipts":
            self._show_receipts()
        elif path.startswith("/receipts/"):
            self._show_receipt(path[len("/receipts/"):])
        else:
            self._send_html(404, "Not Found", _not_found())

    def do_POST(self):
        path = _path(self.path)
        if path == "/items":
            self._add_item()
        elif path == "/charge":
            self._charge()
        else:
            self._send_html(404, "Not Found", _not_found())

    # ------------------------------------------------------------- handlers

    def _show_register(self):
        conn = init_db(self.db_path)
        try:
            items = list_items(conn)
        finally:
            conn.close()

        content = (
            "<h1>Register</h1>\n"
            '<p><a href="/receipts">View past receipts</a></p>\n'
            "<h2>Stock Items</h2>\n"
            "{catalog}\n"
            "<h2>Current Sale</h2>\n"
            "{ticket}\n"
            "<h2>Add Item</h2>\n"
            "{add_form}\n"
        ).format(
            catalog=_catalog_table(items),
            ticket=_ticket_form(items),
            add_form=_add_form(),
        )
        self._send_html(200, "Register", content)

    def _add_item(self):
        body = self._read_form_body()
        fields = parse_qs(body, keep_blank_values=True)

        name = fields.get("name", [""])[0]
        try:
            price_cents = parse_price_to_cents(fields.get("price", [""])[0])
            quantity = parse_quantity(fields.get("quantity", [""])[0])
        except ValueError as exc:
            self._send_html(400, "Bad Request", _error_message(str(exc)))
            return

        conn = init_db(self.db_path)
        try:
            add_item(conn, name, price_cents, quantity)
            conn.commit()
        except ValueError as exc:  # e.g. blank or duplicate name from the store
            conn.rollback()
            self._send_html(400, "Bad Request", _error_message(str(exc)))
            return
        finally:
            conn.close()

        self.send_response(303)  # See Other
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _charge(self):
        """POST /charge: validate + record the sale, redirect to the receipt."""
        body = self._read_form_body()
        fields = parse_qs(body, keep_blank_values=True)

        payment_method = fields.get("payment_method", [""])[0]
        payment_ref = (fields.get("payment_ref", [""])[0] or "").strip() or None
        cash_received = (fields.get("cash_received", [""])[0] or "").strip()

        try:
            lines = _parse_ticket_lines(fields)
        except ValueError as exc:
            self._send_html(
                400, "Bad Request",
                _error_message(str(exc), "Could not charge sale"),
            )
            return

        if not lines:
            self._send_html(
                400, "Bad Request",
                _error_message(
                    "Select at least one item with a quantity to charge.",
                    "Could not charge sale",
                ),
            )
            return
        if payment_method not in PAYMENT_METHODS:
            self._send_html(
                400, "Bad Request",
                _error_message(
                    "Select a valid payment method.", "Could not charge sale"
                ),
            )
            return

        # Cash: an optional "cash received" amount rides along in payment_ref
        # (the only optional TEXT slot the store persists) so the receipt can
        # render the change. It is sanity-checked against the catalog total
        # before checkout so an underpayment never writes a transaction.
        tendered_cents = None
        if payment_method == "cash" and cash_received:
            try:
                tendered_cents = parse_price_to_cents(cash_received)
            except ValueError as exc:
                self._send_html(
                    400, "Bad Request",
                    _error_message(
                        "Cash received: {}".format(exc), "Could not charge sale"
                    ),
                )
                return

        conn = init_db(self.db_path)
        try:
            if tendered_cents is not None:
                tentative = _tentative_total(conn, lines)
                if tentative is not None and tendered_cents < tentative:
                    self._send_html(
                        400, "Bad Request",
                        _error_message(
                            "Cash received must cover the sale total ({}).".format(
                                format_price(tentative)
                            ),
                            "Could not charge sale",
                        ),
                    )
                    return
                payment_ref = str(tendered_cents)
            try:
                txn = checkout(conn, lines, payment_method, payment_ref)
            except ValueError as exc:
                # Insufficient stock, unknown item, invalid qty, unknown
                # method: checkout rolled back, nothing was written.
                self._send_html(
                    400, "Bad Request",
                    _error_message(str(exc), "Could not charge sale"),
                )
                return
        finally:
            conn.close()

        self.send_response(303)  # See Other
        self.send_header("Location", "/receipts/{}".format(txn["receipt_no"]))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _show_receipt(self, receipt_no):
        conn = init_db(self.db_path)
        try:
            txn = get_transaction(conn, receipt_no)
        finally:
            conn.close()

        if txn is None:
            self._send_html(404, "Not Found", _not_found())
            return
        title = "Receipt {}".format(txn["receipt_no"])
        self._send_html(200, title, _receipt_html(txn))

    def _show_receipts(self):
        conn = init_db(self.db_path)
        try:
            txns = list_transactions(conn)
        finally:
            conn.close()

        if txns:
            rows = "\n".join(
                "<tr><td><a href=\"/receipts/{no}\">{no}</a></td>"
                "<td>{date}</td><td>{total}</td></tr>".format(
                    no=html.escape(t["receipt_no"]),
                    date=html.escape(t["created_at"]),
                    total=format_price(t["total_cents"]),
                )
                for t in txns
            )
            table = (
                "<table>\n"
                "<thead><tr><th>Receipt</th><th>Date</th><th>Total</th></tr></thead>\n"
                "<tbody>\n{}\n</tbody>\n"
                "</table>"
            ).format(rows)
        else:
            table = '<p class="empty">No sales yet — charge a sale from the register.</p>'

        content = (
            "<h1>Past Receipts</h1>\n"
            '<p><a href="/">&larr; Back to register</a></p>\n'
            "{}\n"
        ).format(table)
        self._send_html(200, "Past Receipts", content)

    # ------------------------------------------------------------- helpers

    def _read_form_body(self):
        """Read and UTF-8 decode the urlencoded request body."""
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8", "replace")

    def _send_html(self, status, title, content):
        body = _page(title, content).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _path(url):
    """Path component of a request URL, without any query string."""
    return url.split("?", 1)[0]


def _catalog_table(items):
    """Render the stock catalog table (or the empty-state message)."""
    if not items:
        return '<p class="empty">No items yet — add your first item below.</p>'
    rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(item["name"]),
            format_price(item["price_cents"]),
            item["quantity"],
        )
        for item in items
    )
    return (
        "<table>\n"
        "<thead><tr><th>Name</th><th>Price</th><th>Quantity</th></tr></thead>\n"
        "<tbody>\n{}\n</tbody>\n"
        "</table>"
    ).format(rows)


def _ticket_form(items):
    """Render the current-sale ticket: line rows + payment method + buttons."""
    options = ['<option value="">— select item —</option>']
    options.extend(
        '<option value="{id}">{name} — {price}</option>'.format(
            id=item["id"],
            name=html.escape(item["name"]),
            price=format_price(item["price_cents"]),
        )
        for item in items
    )
    options_html = "\n".join(options)

    line_rows = "\n".join(
        (
            '<div class="ticket-line">\n'
            '<select name="item_id">{}</select>\n'
            '<input type="number" name="quantity" min="1" step="1" '
            'placeholder="Qty" value="">\n'
            "</div>"
        ).format(options_html)
        for _ in range(TICKET_LINE_ROWS)
    )

    method_options = "\n".join(
        '<option value="{}">{}</option>'.format(
            html.escape(method),
            html.escape(PAYMENT_METHOD_LABELS.get(method, method)),
        )
        for method in PAYMENT_METHODS
    )

    return (
        '<form class="ticket" method="post" action="/charge">\n'
        "<p class=\"muted\">Items on this sale:</p>\n"
        "{line_rows}\n"
        '<div class="actions">\n'
        '<label>Payment method\n'
        '<select name="payment_method">{method_options}</select>\n'
        "</label>\n"
        '<label>Reference (optional)\n'
        '<input type="text" name="payment_ref" placeholder="e.g. card auth code"></label>\n'
        '<label>Cash received (optional, for cash)\n'
        '<input type="text" name="cash_received" inputmode="decimal" '
        'placeholder="e.g. 20.00"></label>\n'
        '<p class="muted">Total is calculated when the sale is charged.</p>\n'
        '<button type="submit">Charge sale</button>\n'
        "</div>\n"
        "</form>"
    ).format(line_rows=line_rows, method_options=method_options)


def _parse_ticket_lines(fields):
    """Build checkout ``lines`` from the repeated item_id/quantity fields.

    Rows with a blank item selector are skipped (unfilled ticket lines);
    non-integer ids/quantities raise ``ValueError`` with a clear message.
    """
    lines = []
    for item_id, quantity in zip(
        fields.get("item_id", []), fields.get("quantity", [])
    ):
        item_id = (item_id or "").strip()
        quantity = (quantity or "").strip()
        if not item_id:
            continue
        try:
            item_id = int(item_id)
        except ValueError:
            raise ValueError("Item id must be a whole number, got {!r}.".format(item_id)) from None
        try:
            quantity = int(quantity)
        except ValueError:
            raise ValueError("Quantity must be a whole number, got {!r}.".format(quantity)) from None
        lines.append({"item_id": item_id, "qty": quantity})
    return lines


def _tentative_total(conn, lines):
    """Catalog-price total used to sanity-check cash tendered before checkout.

    Returns ``None`` when a line references an unknown item (checkout will
    then raise the authoritative error).
    """
    total = 0
    for line in lines:
        row = conn.execute(
            "SELECT price_cents FROM items WHERE id = ?", (line["item_id"],)
        ).fetchone()
        if row is None:
            return None
        total += row["price_cents"] * line["qty"]
    return total


def _receipt_html(txn):
    """Render the HTML receipt for one transaction dict."""
    rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(line["name"]),
            line["qty"],
            format_price(line["unit_price_cents"]),
            format_price(line["line_total_cents"]),
        )
        for line in txn["lines"]
    )
    lines_table = (
        "<table>\n"
        "<thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Total</th></tr></thead>\n"
        "<tbody>\n{}\n</tbody>\n"
        "</table>"
    ).format(rows)

    return (
        '<div class="receipt">\n'
        "<h2>Cloud POS</h2>\n"
        '<p class="meta">Receipt {no}<br>{date}</p>\n'
        "{lines}\n"
        '<div class="totals">\n'
        "<p>Subtotal: {subtotal}</p>\n"
        "<p><strong>Total: {total}</strong></p>\n"
        "{payment}\n"
        "</div>\n"
        "</div>\n"
        '<p><a href="/receipts">&larr; All receipts</a> &middot; '
        '<a href="/">&larr; Back to register</a></p>'
    ).format(
        no=html.escape(txn["receipt_no"]),
        date=html.escape(txn["created_at"]),
        lines=lines_table,
        subtotal=format_price(txn["subtotal_cents"]),
        total=format_price(txn["total_cents"]),
        payment=_payment_summary(txn),
    )


def _payment_summary(txn):
    """Payment method + reference/change block shown on the receipt."""
    parts = [
        "<p>Payment: {}</p>".format(
            html.escape(_payment_label(txn["payment_method"]))
        )
    ]
    change = _change_cents(txn)
    if change is not None:
        # Cash sale with a recorded "cash received" amount: show change.
        parts.append(
            "<p>Tendered: {} &nbsp; Change: {}</p>".format(
                format_price(int(txn["payment_ref"])), format_price(change)
            )
        )
    elif txn.get("payment_ref"):
        parts.append(
            "<p>Reference: {}</p>".format(html.escape(txn["payment_ref"]))
        )
    elif txn["payment_method"] == "cash":
        # Cash with no tendered amount recorded: exact payment assumed.
        parts.append("<p>Change: $0.00</p>")
    return "\n".join(parts)


def _change_cents(txn):
    """Change due for a cash sale, from the tendered amount in payment_ref.

    ``/charge`` stores a cash "received" amount (integer cents, as a string)
    in ``payment_ref`` so the receipt can render the change. Returns ``None``
    for non-cash sales or when no tendered amount is recorded.
    """
    if txn.get("payment_method") != "cash":
        return None
    ref = txn.get("payment_ref")
    if not ref:
        return None
    try:
        tendered = int(ref)
    except (TypeError, ValueError):
        return None
    if tendered < 0:
        return None
    return tendered - txn["total_cents"]


def _payment_label(method):
    """Human-readable label for a payment method key."""
    return PAYMENT_METHOD_LABELS.get(method, method)


def _add_form():
    return (
        "<h2>Add Item</h2>\n"
        '<form method="post" action="/items">\n'
        '<label>Name<input type="text" name="name" required autofocus></label>\n'
        '<label>Price (dollars)'
        '<input type="text" name="price" inputmode="decimal" placeholder="e.g. 3.50" required></label>\n'
        '<label>Quantity<input type="number" name="quantity" min="0" step="1" value="0"></label>\n'
        '<button type="submit">Add item</button>\n'
        "</form>"
    )


def _error_message(message, action="Could not add item"):
    return (
        '<p class="error">{}: {}</p>\n'
        '<p><a href="/">&larr; Back to register</a></p>'
    ).format(html.escape(action), html.escape(str(message)))


def _not_found():
    return '<p>Page not found. <a href="/">Back to register</a>.</p>'