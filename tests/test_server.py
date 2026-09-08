"""End-to-end happy-path tests for pos.server using the real HTTP handler.

Covers the catalog routes (baseline) and the cashiering surface:
``POST /charge`` (AC3/AC4), ``GET /receipts/{receipt_no}`` and
``GET /receipts`` (AC5).
"""

import io
import os
import tempfile
import unittest
from urllib.parse import urlencode

from pos.server import POSHandler
from pos.store import get_transaction, init_db, list_items, list_transactions


class _WriteBuffer(io.BytesIO):
    """BytesIO whose close() is a no-op so the response stays readable."""

    def close(self):
        pass


class _TestPOSHandler(POSHandler):
    """Drives one request against in-memory buffers instead of a real socket.

    ``setup``/``finish`` are overridden so the handler reads the raw request
    from a BytesIO and writes the response into one we can inspect, while
    keeping every route/validation of the real handler.
    """

    def __init__(self, raw_request):
        self._raw_request = raw_request
        self.response = None
        super().__init__(None, ("127.0.0.1", 0), None)

    def setup(self):
        self.connection = self.request
        self.rfile = io.BytesIO(self._raw_request)
        self.wfile = _WriteBuffer()

    def finish(self):
        self.response = self.wfile.getvalue()

    def log_message(self, fmt, *args):
        # Silence per-request stderr logging during tests.
        pass


def _send(method, path, form=None):
    """Drive one HTTP request through the real handler; return (status, headers, body)."""
    body = urlencode(form or {}).encode("ascii")
    head = [
        "%s %s HTTP/1.1" % (method, path),
        "Host: test",
        "Connection: close",
    ]
    if body:
        head.append("Content-Length: %d" % len(body))
    raw = ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body

    handler = _TestPOSHandler(raw)
    return _parse_response(handler.response)


def _parse_response(raw):
    """Split a raw HTTP response into (status, headers-dict, decoded-body)."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    _version, status, _reason = lines[0].decode("ascii").split(" ", 2)
    headers = {}
    for line in lines[1:]:
        name, _, value = line.decode("ascii").partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(status), headers, body.decode("utf-8")


class POSHandlerTestCase(unittest.TestCase):
    """Each test uses its own SQLite file via the handler's db_path hook."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self._original_db_path = POSHandler.db_path
        POSHandler.db_path = self.db_path

    def tearDown(self):
        POSHandler.db_path = self._original_db_path
        self._tmpdir.cleanup()

    def test_index_shows_empty_state_when_no_items(self):
        """GET / on a fresh database renders the empty-state page with the form."""
        status, _headers, body = _send("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn("No items yet", body)
        self.assertIn("Add Item", body)

    def test_add_item_then_list_shows_it(self):
        """POST /items with valid data redirects, and GET / then lists the item."""
        status, headers, body = _send(
            "POST", "/items",
            {"name": "Cold Brew", "price": "3.50", "quantity": "12"},
        )

        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/")
        self.assertEqual(body, "")

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Cold Brew", body)
        self.assertIn("$3.50", body)
        self.assertIn(">12<", body)
        self.assertNotIn("No items yet", body)

    def test_items_appear_appended_in_insertion_order(self):
        """Two successful adds both appear, later items appended after earlier ones."""
        for name, price in (("Apple", "1.00"), ("Banana", "0.50")):
            status, _headers, _body = _send(
                "POST", "/items",
                {"name": name, "price": price, "quantity": "2"},
            )
            self.assertEqual(status, 303)

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertLess(body.index("Apple"), body.index("Banana"))

    def test_add_without_quantity_defaults_to_zero(self):
        """An add that omits the quantity field stores a 0 quantity (design default)."""
        status, headers, body = _send(
            "POST", "/items",
            {"name": "Tote Bag", "price": "20.00"},
        )

        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/")
        self.assertEqual(body, "")

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Tote Bag", body)
        self.assertIn(">0<", body)

    def test_invalid_submissions_are_rejected_with_400(self):
        """Blank/duplicate name, bad price and bad quantity get a 400 and insert nothing."""
        ok, _h, _b = _send(
            "POST", "/items",
            {"name": "Milk", "price": "3.20", "quantity": "1"},
        )
        self.assertEqual(ok, 303)

        invalid_forms = [
            # Blank name.
            {"name": "   ", "price": "1.00", "quantity": "1"},
            # Missing name field entirely (treated as blank).
            {"price": "1.00", "quantity": "1"},
            # Duplicate name (case-insensitive).
            {"name": "milk", "price": "9.99", "quantity": "1"},
            # Negative price.
            {"name": "Muffin", "price": "-0.50", "quantity": "1"},
            # Non-numeric price.
            {"name": "Muffin", "price": "abc", "quantity": "1"},
            # Price with more than two decimal places.
            {"name": "Muffin", "price": "3.505", "quantity": "1"},
            # Negative quantity.
            {"name": "Muffin", "price": "1.00", "quantity": "-2"},
            # Non-integer quantity.
            {"name": "Muffin", "price": "1.00", "quantity": "1.5"},
            # Missing price field.
            {"name": "Muffin", "quantity": "1"},
        ]

        conn = init_db(self.db_path)
        try:
            for form in invalid_forms:
                status, _headers, body = _send("POST", "/items", form)
                self.assertEqual(status, 400, msg="form=%r" % form)
                self.assertIn("Could not add item", body)
                # The rejected row must not have been inserted.
                self.assertEqual(len(list_items(conn)), 1, msg="form=%r" % form)
        finally:
            conn.close()


class CashieringTestCase(unittest.TestCase):
    """End-to-end checkout coverage through the real HTTP handler."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self._original_db_path = POSHandler.db_path
        POSHandler.db_path = self.db_path
        self.ids = self._seed_catalog()

    def tearDown(self):
        POSHandler.db_path = self._original_db_path
        self._tmpdir.cleanup()

    def _seed_catalog(self):
        """Add the standard test items via the real route; return id by name."""
        for name, price, quantity in (
            ("Milk Tea", "2.40", "10"),
            ("Cookie", "1.50", "5"),
        ):
            status, _headers, _body = _send(
                "POST", "/items",
                {"name": name, "price": price, "quantity": quantity},
            )
            self.assertEqual(status, 303)
        conn = init_db(self.db_path)
        try:
            return dict((item["name"], item["id"]) for item in list_items(conn))
        finally:
            conn.close()

    def _charge(self, lines, payment_method, **extra):
        """POST /charge with repeated item_id/quantity fields; return (status, headers, body)."""
        form = []
        for item_id, qty in lines:
            form.append(("item_id", str(item_id)))
            form.append(("quantity", str(qty)))
        form.append(("payment_method", payment_method))
        for key, value in extra.items():
            form.append((key, value))
        return _send("POST", "/charge", form)

    def _quantities(self):
        conn = init_db(self.db_path)
        try:
            return dict((item["id"], item["quantity"]) for item in list_items(conn))
        finally:
            conn.close()

    # ------------------------------------------------------- register page (AC2)

    def test_register_page_renders_ticket_form_and_payment_methods(self):
        """GET / renders the current-sale ticket with every payment choice."""
        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Register", body)
        self.assertIn("Current Sale", body)
        self.assertIn('action="/charge"', body)
        self.assertIn('name="item_id"', body)
        self.assertIn('name="quantity"', body)
        self.assertIn('name="payment_method"', body)
        for method in (
            "cash", "card", "octopus", "fps", "alipayhk",
            "wechatpayhk", "payme", "store-credit",
        ):
            self.assertIn('value="{}"'.format(method), body, msg=method)

    # ------------------------------------------------------- happy path (AC3)

    def test_charge_redirects_to_receipt_decrements_stock_and_persists(self):
        """A valid charge: 303 -> /receipts/{no}, stock decremented, txn persisted."""
        status, headers, body = self._charge(
            [(self.ids["Milk Tea"], 2)], "cash"
        )
        self.assertEqual(status, 303)
        location = headers.get("location", "")
        self.assertRegex(location, r"^/receipts/\d{4}-\d{4}$")
        self.assertEqual(body, "")
        receipt_no = location.rsplit("/", 1)[-1]

        conn = init_db(self.db_path)
        try:
            txn = get_transaction(conn, receipt_no)
        finally:
            conn.close()
        self.assertIsNotNone(txn)
        self.assertEqual(txn["subtotal_cents"], 480)
        self.assertEqual(txn["total_cents"], 480)
        self.assertEqual(txn["payment_method"], "cash")
        self.assertEqual(len(txn["lines"]), 1)
        self.assertEqual(txn["lines"][0]["name"], "Milk Tea")
        self.assertEqual(txn["lines"][0]["qty"], 2)
        self.assertEqual(txn["lines"][0]["line_total_cents"], 480)

        quantities = self._quantities()
        self.assertEqual(quantities[self.ids["Milk Tea"]], 8)  # 10 - 2
        self.assertEqual(quantities[self.ids["Cookie"]], 5)

    def test_multi_line_charge_persists_every_line(self):
        """A two-line ticket persists both lines and the server-computed total."""
        status, headers, _body = self._charge(
            [(self.ids["Milk Tea"], 2), (self.ids["Cookie"], 3)], "card"
        )
        self.assertEqual(status, 303)
        receipt_no = headers["location"].rsplit("/", 1)[-1]

        conn = init_db(self.db_path)
        try:
            txn = get_transaction(conn, receipt_no)
        finally:
            conn.close()
        self.assertIsNotNone(txn)
        self.assertEqual(txn["total_cents"], 240 * 2 + 150 * 3)
        self.assertEqual(len(txn["lines"]), 2)
        self.assertEqual(txn["lines"][0]["name"], "Milk Tea")
        self.assertEqual(txn["lines"][1]["name"], "Cookie")

    # ------------------------------------------------------- receipts (AC5)

    def test_receipt_page_renders_the_sale(self):
        """GET /receipts/{no} renders receipt number, lines, totals, payment."""
        status, headers, _body = self._charge(
            [(self.ids["Milk Tea"], 2)], "card", payment_ref="REF123"
        )
        self.assertEqual(status, 303)
        receipt_no = headers["location"].rsplit("/", 1)[-1]

        status, _headers, body = _send("GET", "/receipts/{}".format(receipt_no))
        self.assertEqual(status, 200)
        self.assertIn(receipt_no, body)
        self.assertIn("Milk Tea", body)
        self.assertIn("$2.40", body)   # unit price
        self.assertIn("$4.80", body)   # line + total
        self.assertIn("Subtotal", body)
        self.assertIn("Payment: Card", body)
        self.assertIn("Reference: REF123", body)

    def test_cash_charge_with_tendered_amount_renders_change(self):
        """A cash sale with a tendered amount shows tendered + change on the receipt."""
        status, headers, _body = self._charge(
            [(self.ids["Milk Tea"], 2)], "cash", cash_received="10.00"
        )
        self.assertEqual(status, 303)
        receipt_no = headers["location"].rsplit("/", 1)[-1]

        status, _headers, body = _send("GET", "/receipts/{}".format(receipt_no))
        self.assertEqual(status, 200)
        self.assertIn("Tendered: $10.00", body)
        self.assertIn("Change: $5.20", body)  # 10.00 - 4.80

    def test_unknown_receipt_returns_404(self):
        """An unknown receipt number renders a 404 page."""
        status, _headers, body = _send("GET", "/receipts/1999-0001")
        self.assertEqual(status, 404)
        self.assertIn("Not Found", body)

    def test_receipts_page_lists_sales_newest_first(self):
        """GET /receipts lists past sales (newest first) with links to each."""
        receipts = []
        for lines in (
            [(self.ids["Milk Tea"], 1)],
            [(self.ids["Cookie"], 1)],
        ):
            status, headers, _body = self._charge(lines, "cash")
            self.assertEqual(status, 303)
            receipts.append(headers["location"].rsplit("/", 1)[-1])
        self.assertEqual(len(set(receipts)), 2)

        status, _headers, body = _send("GET", "/receipts")
        self.assertEqual(status, 200)
        self.assertIn("Past Receipts", body)
        for receipt_no in receipts:
            self.assertIn(receipt_no, body)
        # Newest first: the second sale appears above the first.
        self.assertLess(body.index(receipts[1]), body.index(receipts[0]))

    # ------------------------------------------------------- rejections (AC4)

    def test_rejected_charges_return_400_and_write_nothing(self):
        """Insufficient stock, unknown item, bad qty and unknown payment method
        get a 400 with no transaction row and no stock change."""
        bad_charges = [
            # Insufficient stock.
            ([(self.ids["Cookie"], 99)], "cash"),
            # Unknown item id.
            ([(9999, 1)], "cash"),
            # Non-positive quantities.
            ([(self.ids["Milk Tea"], 0)], "cash"),
            ([(self.ids["Milk Tea"], -1)], "cash"),
            # Unknown payment method.
            ([(self.ids["Milk Tea"], 1)], "bitcoin"),
        ]

        conn = init_db(self.db_path)
        try:
            for lines, method in bad_charges:
                status, _headers, body = self._charge(lines, method)
                self.assertEqual(status, 400, msg="lines=%r method=%r" % (lines, method))
                self.assertIn("Could not charge sale", body)
                self.assertEqual(
                    list_transactions(conn), [],
                    msg="transaction written for lines=%r" % (lines,),
                )
                quantities = self._quantities()
                self.assertEqual(
                    quantities[self.ids["Milk Tea"]], 10, msg="lines=%r" % (lines,)
                )
                self.assertEqual(
                    quantities[self.ids["Cookie"]], 5, msg="lines=%r" % (lines,)
                )
        finally:
            conn.close()

    def test_cash_underpayment_is_rejected_with_400(self):
        """Cash received below the sale total is rejected and writes nothing."""
        status, _headers, body = self._charge(
            [(self.ids["Milk Tea"], 2)], "cash", cash_received="3.00"
        )
        self.assertEqual(status, 400)
        self.assertIn("Cash received must cover", body)

        conn = init_db(self.db_path)
        try:
            self.assertEqual(list_transactions(conn), [])
        finally:
            conn.close()
        self.assertEqual(self._quantities()[self.ids["Milk Tea"]], 10)


if __name__ == "__main__":
    unittest.main()
