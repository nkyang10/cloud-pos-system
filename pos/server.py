"""POS catalog HTTP endpoints (stdlib only).

Routes
------
GET  /                  -> 200 HTML page: table of stock items, each row with
                           an inline "Adjust" (stock in/out) form, plus an
                           "Add item" form (empty-state message when empty).
POST /items             -> parse the ``application/x-www-form-urlencoded`` body,
                           validate, insert via ``pos.store``, then reply
                           303 See Other -> "/" on success, or 400 HTML with a
                           clear message on failure.
POST /items/{id}/adjust -> parse ``{id}`` from the path and the ``delta`` /
                           ``movement_type`` fields from the body, validate,
                           adjust via ``pos.store.adjust_quantity``, then reply
                           303 See Other -> "/" on success, 400 HTML with a
                           clear message on invalid / negative-result input, or
                           404 for an unknown item or any other path.
"""

import html
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from pos.store import add_item, init_db, list_items

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
  input { padding: .4rem .5rem; font-size: 1rem; border: 1px solid #bbb;
          border-radius: 4px; }
  button { justify-self: start; padding: .5rem 1.25rem; font-size: 1rem;
           cursor: pointer; }
  form.adjust { display: flex; gap: .35rem; max-width: none; margin: 0;
                align-items: center; white-space: nowrap; }
  form.adjust input, form.adjust select { padding: .3rem .45rem; font-size: .9rem;
                border: 1px solid #bbb; border-radius: 4px; }
  form.adjust input[type="number"] { width: 5.5rem; }
  form.adjust button { padding: .3rem .8rem; font-size: .9rem; }
  select { font-family: inherit; }
  a { color: #06c; }
"""


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


def parse_delta(raw):
    """Parse an adjust ``delta`` form field into a signed, non-zero int.

    Accepts values like ``"10"``, ``"+10"`` or ``"-2"``. Blank or
    non-integer input (including ``"0"``, which cannot change stock) raises
    ``ValueError`` with a user-facing message. The sign-vs-movement_type
    match and the non-negative on-hand rule are enforced by the store.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Delta is required.")
    try:
        value = int(text)
    except ValueError:
        raise ValueError(
            "Delta must be a whole number like 10 or -2, got {!r}.".format(text)
        ) from None
    if value == 0:
        raise ValueError("Delta must not be zero.")
    return value


def parse_movement_type(raw):
    """Normalize an adjust ``movement_type`` form field to ``'in'``/``'out'``.

    Matching is case-insensitive; anything else raises ``ValueError``.
    """
    text = (raw or "").strip().lower()
    if text not in ("in", "out"):
        raise ValueError("Movement type must be either 'in' or 'out'.")
    return text


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
    """Serves the POS stock-items list with per-row Adjust forms plus add-item."""

    # SQLite file the handler reads/writes; tests can override this attribute.
    db_path = "stock.db"

    # ------------------------------------------------------------- HTTP verbs

    def do_GET(self):
        if _path(self.path) != "/":
            self._send_html(404, "Not Found", _not_found())
            return
        self._show_items()

    def do_POST(self):
        path = _path(self.path)
        if path == "/items":
            self._add_item()
            return
        item_id = _adjust_route_item_id(path)
        if item_id is not None:
            self._adjust_item(item_id)
            return
        self._send_html(404, "Not Found", _not_found())

    # ------------------------------------------------------------- handlers

    def _show_items(self):
        conn = init_db(self.db_path)
        try:
            items = list_items(conn)
        finally:
            conn.close()

        if items:
            rows = "\n".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(item["name"]),
                    format_price(item["price_cents"]),
                    item["quantity"],
                    _adjust_form(item["id"]),
                )
                for item in items
            )
            table = (
                "<table>\n"
                "<thead><tr><th>Name</th><th>Price</th><th>Quantity</th>"
                "<th>Adjust</th></tr></thead>\n"
                "<tbody>\n{}\n</tbody>\n"
                "</table>"
            ).format(rows)
        else:
            table = '<p class="empty">No items yet — add your first item below.</p>'

        content = "<h1>Stock Items</h1>{}{}".format(
            table, _add_form()
        )
        self._send_html(200, "Stock Items", content)

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
            # ``with conn:`` commits on success and rolls back on any
            # exception (e.g. blank or duplicate name from the store).
            with conn:
                add_item(conn, name, price_cents, quantity)
        except ValueError as exc:
            self._send_html(400, "Bad Request", _error_message(str(exc)))
            return
        finally:
            conn.close()

        self.send_response(303)  # See Other
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _adjust_item(self, item_id):
        # Imported here (not at module top) so this module still imports on
        # branches where ``pos.store`` has not yet gained the adjust APIs;
        # the store layer lands first at merge time.
        from pos.store import ItemNotFoundError, adjust_quantity

        body = self._read_form_body()
        fields = parse_qs(body, keep_blank_values=True)

        try:
            delta = parse_delta(fields.get("delta", [""])[0])
            movement_type = parse_movement_type(fields.get("movement_type", [""])[0])
        except ValueError as exc:
            self._send_html(400, "Bad Request", _adjust_error(str(exc)))
            return

        conn = init_db(self.db_path)
        try:
            # adjust_quantity runs its own ``with conn:`` transaction.
            adjust_quantity(conn, item_id, delta, movement_type)
        except ItemNotFoundError:
            self._send_html(404, "Not Found", _not_found())
            return
        except ValueError as exc:  # sign/type mismatch or net-negative result
            self._send_html(400, "Bad Request", _adjust_error(str(exc)))
            return
        finally:
            conn.close()

        self.send_response(303)  # See Other
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

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


def _adjust_route_item_id(url):
    """Return the numeric item id for a ``/items/<id>/adjust`` path, else None.

    ``None`` means the path is not an adjust route (e.g. ``/items``,
    ``/items/5``, ``/items/abc/adjust`` or anything else) and should 404.
    """
    prefix = "/items/"
    suffix = "/adjust"
    if not url.startswith(prefix) or not url.endswith(suffix):
        return None
    raw = url[len(prefix):-len(suffix)]
    if not raw.isdigit():
        return None
    return int(raw)


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


def _adjust_form(item_id):
    """Inline per-row "Adjust" form posting to ``/items/<id>/adjust``."""
    return (
        '<form class="adjust" method="post" action="/items/{}/adjust">\n'
        '<input type="number" name="delta" step="1" required '
        'placeholder="e.g. +5" title="Signed quantity change (e.g. +10 in, -2 out)">\n'
        '<select name="movement_type" title="Movement type">\n'
        '<option value="in">In</option>\n'
        '<option value="out">Out</option>\n'
        "</select>\n"
        '<button type="submit">Adjust</button>\n'
        "</form>"
    ).format(item_id)


def _error_message(message):
    return (
        '<p class="error">Could not add item: {}</p>\n'
        '<p><a href="/">&larr; Back to items</a></p>'
    ).format(html.escape(str(message)))


def _adjust_error(message):
    return (
        '<p class="error">Could not adjust item: {}</p>\n'
        '<p><a href="/">&larr; Back to items</a></p>'
    ).format(html.escape(str(message)))


def _not_found():
    return '<p>Page not found. <a href="/">Back to items</a>.</p>'
