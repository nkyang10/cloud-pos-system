"""End-to-end happy-path tests for pos.server using the real HTTP handler."""

import io
import os
import tempfile
import unittest
from urllib.parse import urlencode

from pos.server import POSHandler
from pos.store import init_db, list_items


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


if __name__ == "__main__":
    unittest.main()
