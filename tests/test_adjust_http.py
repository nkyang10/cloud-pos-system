"""End-to-end HTTP tests for the stock-quantity adjust flow.

Covers ``POST /items/{id}/adjust`` (PRD AC6-AC7) through the real
``POSHandler``:

- Happy path: a valid stock-in / stock-out returns ``303 See Other -> /`` and
  ``GET /`` renders the updated on-hand quantity (and the per-row Adjust form).
- Rejections: a net-negative stock-out returns ``400`` with the quantity
  unchanged and no movement row recorded; bad or missing ``delta`` /
  ``movement_type`` (including a sign/type mismatch) returns ``400``; an
  unknown item id or any other unknown path returns ``404``.
- Persistence (AC7): committed adjustments and the ledger survive reopening the
  database.
"""

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


def _movement_rows(db_path):
    """Return the stock_movements ledger rows for a database file (test plumbing)."""
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, item_id, delta, movement_type FROM stock_movements ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


class AdjustHttpTestCase(unittest.TestCase):
    """Each test uses its own SQLite file via the handler's db_path hook."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self._original_db_path = POSHandler.db_path
        POSHandler.db_path = self.db_path

    def tearDown(self):
        POSHandler.db_path = self._original_db_path
        self._tmpdir.cleanup()

    def _add_item(self, name="Coffee", price="3.50", quantity="10"):
        """Add an item via the real add route and return its id."""
        status, headers, _body = _send(
            "POST", "/items",
            {"name": name, "price": price, "quantity": quantity},
        )
        self.assertEqual(status, 303, msg="add %r" % name)
        self.assertEqual(headers.get("location"), "/")
        conn = init_db(self.db_path)
        try:
            items = list_items(conn)
        finally:
            conn.close()
        return next(item["id"] for item in items if item["name"] == name)

    def _adjust(self, item_id, delta, movement_type):
        """POST the adjust route; returns (status, headers, body)."""
        return _send(
            "POST", "/items/{}/adjust".format(item_id),
            {"delta": delta, "movement_type": movement_type},
        )

    # -- happy path ------------------------------------------------------

    def test_adjust_in_redirects_and_updates_quantity_on_get(self):
        """A valid stock-in -> 303 -> GET / renders the new (higher) quantity."""
        item_id = self._add_item("Coffee", quantity="10")

        status, headers, _body = self._adjust(item_id, "+5", "in")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/")

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">15<", body)
        # The per-row Adjust form for this item is rendered on the list page.
        self.assertIn('action="/items/{}/adjust"'.format(item_id), body)

    def test_adjust_out_redirects_and_updates_quantity_on_get(self):
        """A valid stock-out -> 303 -> GET / renders the new (lower) quantity."""
        item_id = self._add_item("Coffee", quantity="10")

        status, headers, _body = self._adjust(item_id, "-4", "out")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/")

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">6<", body)

    # -- rejections ------------------------------------------------------

    def test_net_negative_adjust_returns_400_and_changes_nothing(self):
        """A stock-out that would drive on-hand below zero is a 400 (AC4/AC6)."""
        item_id = self._add_item("Coffee", quantity="2")

        status, _headers, body = self._adjust(item_id, "-5", "out")
        self.assertEqual(status, 400)
        self.assertIn("Could not adjust item", body)

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">2<", body)
        self.assertEqual(_movement_rows(self.db_path), [])

    def test_unknown_item_id_returns_404(self):
        """Adjusting a non-existent item id replies 404 and records nothing."""
        status, _headers, body = self._adjust(999, "5", "in")

        self.assertEqual(status, 404)
        self.assertIn("Page not found", body)
        self.assertEqual(_movement_rows(self.db_path), [])

    def test_invalid_delta_or_movement_type_returns_400(self):
        """Bad/missing delta or movement_type -> 400, nothing changes (AC6)."""
        item_id = self._add_item("Coffee", quantity="10")

        bad_forms = [
            {},  # both fields missing
            {"delta": "5"},  # movement_type missing
            {"movement_type": "in"},  # delta missing
            {"delta": "", "movement_type": "in"},  # blank delta
            {"delta": "0", "movement_type": "in"},  # zero delta
            {"delta": "abc", "movement_type": "in"},  # non-integer delta
            {"delta": "1.5", "movement_type": "in"},  # non-integer delta
            {"delta": "5", "movement_type": "out"},  # sign/type mismatch
            {"delta": "-5", "movement_type": "in"},  # sign/type mismatch
            {"delta": "5", "movement_type": "restock"},  # bad movement_type
            {"delta": "5", "movement_type": ""},  # blank movement_type
        ]
        for form in bad_forms:
            status, _headers, body = _send(
                "POST", "/items/{}/adjust".format(item_id), form
            )
            self.assertEqual(status, 400, msg="form=%r" % form)
            self.assertIn("Could not adjust item", body)
            self.assertEqual(_movement_rows(self.db_path), [], msg="form=%r" % form)

        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">10<", body)

    def test_unknown_paths_return_404(self):
        """Any path other than the known routes replies 404 (AC6)."""
        unknown = [
            ("GET", "/nope", None),
            ("POST", "/nope", {"delta": "5", "movement_type": "in"}),
            ("POST", "/items/abc/adjust", {"delta": "5", "movement_type": "in"}),
            ("POST", "/items/5", {"delta": "5", "movement_type": "in"}),
            ("POST", "/items/5/adjust/extra", {"delta": "5", "movement_type": "in"}),
        ]
        for method, path, form in unknown:
            status, _headers, body = _send(method, path, form)
            self.assertEqual(status, 404, msg="%s %s" % (method, path))
            self.assertIn("Page not found", body)

    # -- persistence -----------------------------------------------------

    def test_adjustments_persist_across_a_reopen(self):
        """Committed adjustments and the ledger survive a reopen (AC7)."""
        item_id = self._add_item("Coffee", quantity="10")

        status, _headers, _body = self._adjust(item_id, "+6", "in")
        self.assertEqual(status, 303)
        status, _headers, _body = self._adjust(item_id, "-3", "out")
        self.assertEqual(status, 303)

        # Every request opens a fresh SQLite connection, so a new request
        # sequence exercises the same code path as a server restart.
        status, _headers, body = _send("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(">13<", body)

        rows = _movement_rows(self.db_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["delta"] for r in rows], [6, -3])
        self.assertEqual([r["movement_type"] for r in rows], ["in", "out"])

        conn = init_db(self.db_path)
        try:
            quantity = next(
                item["quantity"] for item in list_items(conn) if item["id"] == item_id
            )
        finally:
            conn.close()
        self.assertEqual(quantity, 13)


if __name__ == "__main__":
    unittest.main()