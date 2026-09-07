"""Store-level tests for the stock-movement ledger and ``adjust_quantity``.

Covers the stock-quantity adjust feature (PRD AC2-AC5, AC7):

- Happy path: an in/out adjustment inserts a ``stock_movements`` row, updates
  ``items.quantity`` in the same transaction, and returns the new on-hand;
  ``list_movements`` returns the ledger rows (ordered by id, optionally
  filtered by ``item_id``).
- Invariant (AC5): after any sequence of adjustments, each adjusted item's
  ``quantity`` equals ``SUM(delta)`` of its movement rows.
- Rejections (AC3/AC4): an unknown item raises ``ItemNotFoundError`` (a
  ``ValueError`` subclass); a net-negative result, a sign/type mismatch, a
  zero/non-integer delta, or a bad ``movement_type`` raises ``ValueError`` --
  each rejection records no movement row and leaves the quantity unchanged.
- Persistence (AC7): committed adjustments and the ledger survive closing and
  reopening the database file.
"""

import os
import tempfile
import unittest

from pos.store import (
    ItemNotFoundError,
    add_item,
    adjust_quantity,
    init_db,
    list_items,
    list_movements,
)


def _movement_sum(movements):
    """Return SUM(delta) of a list of movement-ledger dicts."""
    return sum(m["delta"] for m in movements)


class MovementsTestCase(unittest.TestCase):
    """Exercises the ledger + adjust_quantity against a throwaway SQLite file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def _add_item(self, name="Coffee", quantity=0):
        item_id = add_item(self.conn, name, price_cents=300, quantity=quantity)
        self.conn.commit()
        return item_id

    def _quantity(self, item_id):
        items = list_items(self.conn)
        return next(item["quantity"] for item in items if item["id"] == item_id)

    # -- happy path ------------------------------------------------------

    def test_adjust_in_inserts_movement_row_and_returns_new_quantity(self):
        """An 'in' adjustment records a positive-delta row and returns the new on-hand."""
        item_id = self._add_item("Coffee", quantity=10)

        new_quantity = adjust_quantity(self.conn, item_id, 5, "in")

        self.assertEqual(new_quantity, 15)
        self.assertEqual(self._quantity(item_id), 15)

        movements = list_movements(self.conn, item_id=item_id)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]["item_id"], item_id)
        self.assertEqual(movements[0]["delta"], 5)
        self.assertEqual(movements[0]["movement_type"], "in")
        self.assertTrue(movements[0]["created_at"])  # timestamp column filled

    def test_adjust_out_inserts_movement_row_and_returns_new_quantity(self):
        """An 'out' adjustment records a negative-delta row and returns the new on-hand."""
        item_id = self._add_item("Coffee", quantity=10)

        new_quantity = adjust_quantity(self.conn, item_id, -4, "out")

        self.assertEqual(new_quantity, 6)
        self.assertEqual(self._quantity(item_id), 6)

        movements = list_movements(self.conn, item_id=item_id)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]["delta"], -4)
        self.assertEqual(movements[0]["movement_type"], "out")

    def test_quantity_matches_sum_of_deltas_after_in_and_out(self):
        """After in/out adjustments, quantity == SUM(delta) of the ledger (AC5)."""
        item_id = self._add_item("Coffee")  # starting on-hand 0

        adjust_quantity(self.conn, item_id, 10, "in")
        adjust_quantity(self.conn, item_id, -4, "out")
        adjust_quantity(self.conn, item_id, -1, "out")

        movements = list_movements(self.conn, item_id=item_id)
        self.assertEqual(len(movements), 3)
        self.assertEqual(
            [m["movement_type"] for m in movements],
            ["in", "out", "out"],
        )
        self.assertEqual(_movement_sum(movements), 5)
        self.assertEqual(self._quantity(item_id), 5)

    def test_list_movements_orders_and_filters_by_item(self):
        """list_movements returns rows ordered by id, filtered by item_id when given."""
        item_a = self._add_item("Coffee", quantity=10)
        item_b = self._add_item("Tea", quantity=5)

        adjust_quantity(self.conn, item_a, 2, "in")
        adjust_quantity(self.conn, item_b, -1, "out")
        adjust_quantity(self.conn, item_a, -3, "out")

        a_rows = list_movements(self.conn, item_id=item_a)
        self.assertEqual(len(a_rows), 2)
        self.assertTrue(all(m["item_id"] == item_a for m in a_rows))
        self.assertEqual([m["delta"] for m in a_rows], [2, -3])
        # Rows are ordered by id ascending.
        self.assertEqual(
            [m["id"] for m in a_rows], sorted(m["id"] for m in a_rows)
        )

        self.assertEqual(len(list_movements(self.conn, item_id=item_b)), 1)
        self.assertEqual(len(list_movements(self.conn)), 3)

    # -- rejections ------------------------------------------------------

    def test_unknown_item_raises_item_not_found_error(self):
        """Adjusting a missing item id raises ItemNotFoundError and records nothing."""
        with self.assertRaises(ItemNotFoundError):
            adjust_quantity(self.conn, 999, 5, "in")

        self.assertEqual(list_movements(self.conn), [])

    def test_item_not_found_error_is_a_value_error(self):
        """ItemNotFoundError subclasses ValueError so existing handlers keep working."""
        self.assertTrue(issubclass(ItemNotFoundError, ValueError))

    def test_net_negative_rejection_records_nothing_and_leaves_quantity(self):
        """A stock-out that would drive on-hand below zero is rejected (AC4)."""
        item_id = self._add_item("Coffee", quantity=2)

        with self.assertRaises(ValueError):
            adjust_quantity(self.conn, item_id, -5, "out")

        self.assertEqual(list_movements(self.conn), [])
        self.assertEqual(self._quantity(item_id), 2)

    def test_sign_type_mismatch_is_rejected(self):
        """An 'out' with a positive delta (and vice versa) is a sign mismatch."""
        item_id = self._add_item("Coffee", quantity=10)

        for delta, movement_type in ((5, "out"), (-5, "in")):
            with self.assertRaises(ValueError):
                adjust_quantity(self.conn, item_id, delta, movement_type)

        self.assertEqual(list_movements(self.conn), [])
        self.assertEqual(self._quantity(item_id), 10)

    def test_zero_delta_is_rejected(self):
        """A zero delta is invalid and records no movement row."""
        item_id = self._add_item("Coffee", quantity=10)

        with self.assertRaises(ValueError):
            adjust_quantity(self.conn, item_id, 0, "in")

        self.assertEqual(list_movements(self.conn), [])

    def test_non_integer_delta_is_rejected(self):
        """A non-int delta (string, float, bool) is invalid and records nothing."""
        item_id = self._add_item("Coffee", quantity=10)

        for bad_delta in ("5", 2.5, True):
            with self.assertRaises(ValueError):
                adjust_quantity(self.conn, item_id, bad_delta, "in")

        self.assertEqual(list_movements(self.conn), [])
        self.assertEqual(self._quantity(item_id), 10)

    def test_bad_movement_type_is_rejected(self):
        """A movement_type other than 'in'/'out' is invalid and records nothing."""
        item_id = self._add_item("Coffee", quantity=10)

        for bad_type in ("restock", "IN", None):
            with self.assertRaises(ValueError):
                adjust_quantity(self.conn, item_id, 5, bad_type)

        self.assertEqual(list_movements(self.conn), [])
        self.assertEqual(self._quantity(item_id), 10)

    # -- persistence -----------------------------------------------------

    def test_movements_and_quantity_persist_across_a_reopen(self):
        """Committed adjustments and the ledger survive closing + reopening (AC7)."""
        item_id = self._add_item("Coffee", quantity=10)
        adjust_quantity(self.conn, item_id, 6, "in")
        adjust_quantity(self.conn, item_id, -3, "out")
        self.conn.close()

        reopened = init_db(self.db_path)
        try:
            movements = list_movements(reopened, item_id=item_id)
            self.assertEqual(len(movements), 2)
            self.assertEqual(_movement_sum(movements), 3)
            self.assertEqual(
                next(
                    item["quantity"]
                    for item in list_items(reopened)
                    if item["id"] == item_id
                ),
                13,
            )
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()