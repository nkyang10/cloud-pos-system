"""Happy-path unit tests for pos.store (empty list -> add -> list shows item)."""

import os
import tempfile
import unittest

from pos.store import add_item, init_db, list_items


class StoreTestCase(unittest.TestCase):
    """Exercises the store against a throwaway SQLite file per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def test_new_database_has_no_items(self):
        """An untouched database reports an empty list (no error)."""
        self.assertEqual(list_items(self.conn), [])

    def test_add_item_then_list_shows_it(self):
        """add_item returns an id and the item then appears in list_items."""
        item_id = add_item(self.conn, "Cold Brew", price_cents=450, quantity=12)
        self.conn.commit()

        self.assertIsInstance(item_id, int)
        self.assertEqual(
            list_items(self.conn),
            [{
                "id": item_id,
                "name": "Cold Brew",
                "price_cents": 450,
                "quantity": 12,
            }],
        )

    def test_add_item_defaults_quantity_to_zero(self):
        """Omitting quantity stores a 0 (blank defaults to 0 per the design)."""
        item_id = add_item(self.conn, "Tote Bag", price_cents=2000)
        self.conn.commit()
        items = list_items(self.conn)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Tote Bag")
        self.assertEqual(items[0]["quantity"], 0)

    def test_items_appear_in_insertion_order(self):
        """Items are listed ordered by id (insertion order)."""
        for name, cents in (("Apple", 100), ("Banana", 150), ("Cherry", 200)):
            add_item(self.conn, name, cents)
        self.conn.commit()
        self.assertEqual(
            [item["name"] for item in list_items(self.conn)],
            ["Apple", "Banana", "Cherry"],
        )

    def test_invalid_input_is_rejected(self):
        """Blank/duplicate name, negative price and bad quantity raise and insert nothing."""
        add_item(self.conn, "Milk", price_cents=320, quantity=1)
        self.conn.commit()

        bad_calls = [
            # Blank name.
            lambda: add_item(self.conn, "   ", price_cents=100),
            # Duplicate name, case-insensitively.
            lambda: add_item(self.conn, "milk", price_cents=999),
            # Negative price.
            lambda: add_item(self.conn, "Muffin", price_cents=-50),
            # Negative quantity.
            lambda: add_item(self.conn, "Muffin", price_cents=100, quantity=-1),
            # Non-integer quantity.
            lambda: add_item(self.conn, "Muffin", price_cents=100, quantity="3"),
        ]

        for call in bad_calls:
            with self.assertRaises(ValueError):
                call()
            # A rejected add must not leave a pending row behind.
            self.assertEqual(len(list_items(self.conn)), 1)

        self.conn.rollback()
        self.assertEqual(
            [item["name"] for item in list_items(self.conn)],
            ["Milk"],
        )

    def test_items_persist_across_a_reopen(self):
        """Committed items survive closing and reopening the database file."""
        add_item(self.conn, "Oat Milk", price_cents=420, quantity=3)
        self.conn.commit()
        self.conn.close()

        reopened = init_db(self.db_path)
        try:
            self.assertEqual(
                list_items(reopened),
                [{
                    "id": 1,
                    "name": "Oat Milk",
                    "price_cents": 420,
                    "quantity": 3,
                }],
            )
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
