"""Checkout-flow tests for pos.store (PRD AC3/AC4/AC6).

Covers the atomic sale at the store layer: stock check-and-decrement, unique
sequential receipt numbers, transaction + line persistence (read back via
``get_transaction`` and ``list_transactions``), rejection semantics that write
nothing (AC4), and concurrent charges from two connections producing distinct
receipt numbers with stock that never goes negative (AC6).
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime

from pos.store import (
    add_item,
    checkout,
    get_transaction,
    init_db,
    list_items,
    list_transactions,
)


def _expected_no(n):
    """The receipt number ``n`` mints for the current calendar year."""
    return "{}-{:04d}".format(datetime.now().year, n)


class CheckoutTestCase(unittest.TestCase):
    """Exercises checkout against a throwaway SQLite file per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.conn = init_db(self.db_path)
        self.tea_id = add_item(self.conn, "Milk Tea", price_cents=240, quantity=10)
        self.cookie_id = add_item(self.conn, "Cookie", price_cents=150, quantity=5)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def _quantity(self, item_id):
        """Current stock of ``item_id`` as seen by the test connection."""
        return dict(
            (item["id"], item["quantity"]) for item in list_items(self.conn)
        )[item_id]

    # ------------------------------------------------------- happy path (AC3)

    def test_valid_charge_decrements_stock_and_returns_the_sale(self):
        """A valid multi-line charge decrements stock per line and returns the sale."""
        txn = checkout(
            self.conn,
            [
                {"item_id": self.tea_id, "qty": 3},
                {"item_id": self.cookie_id, "qty": 2},
            ],
            "cash",
            payment_ref="1000",
        )

        self.assertEqual(self._quantity(self.tea_id), 7)
        self.assertEqual(self._quantity(self.cookie_id), 3)

        self.assertEqual(txn["subtotal_cents"], 240 * 3 + 150 * 2)
        self.assertEqual(txn["total_cents"], 240 * 3 + 150 * 2)
        self.assertEqual(txn["payment_method"], "cash")
        self.assertEqual(txn["payment_ref"], "1000")
        self.assertTrue(txn["created_at"])
        self.assertEqual(txn["receipt_year"], datetime.now().year)
        # Lines carry the server-side snapshot (name/price) and line totals.
        self.assertEqual(len(txn["lines"]), 2)
        self.assertEqual(txn["lines"][0]["name"], "Milk Tea")
        self.assertEqual(txn["lines"][0]["unit_price_cents"], 240)
        self.assertEqual(txn["lines"][0]["qty"], 3)
        self.assertEqual(txn["lines"][0]["line_total_cents"], 720)
        self.assertEqual(txn["lines"][1]["name"], "Cookie")
        self.assertEqual(txn["lines"][1]["line_total_cents"], 300)

    def test_receipt_numbers_are_unique_and_sequential(self):
        """Two charges mint distinct, consecutive receipt numbers."""
        first = checkout(self.conn, [{"item_id": self.tea_id, "qty": 1}], "card")
        second = checkout(self.conn, [{"item_id": self.cookie_id, "qty": 1}], "card")

        self.assertEqual(first["receipt_no"], _expected_no(1))
        self.assertEqual(second["receipt_no"], _expected_no(2))
        self.assertNotEqual(first["receipt_no"], second["receipt_no"])

    def test_transaction_and_lines_are_persisted_and_visible(self):
        """The sale is readable back via get_transaction and list_transactions."""
        txn = checkout(
            self.conn,
            [{"item_id": self.tea_id, "qty": 2}],
            "octopus",
            payment_ref="AUTH-42",
        )

        saved = get_transaction(self.conn, txn["receipt_no"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved["receipt_no"], txn["receipt_no"])
        self.assertEqual(saved["subtotal_cents"], 480)
        self.assertEqual(saved["total_cents"], 480)
        self.assertEqual(saved["payment_method"], "octopus")
        self.assertEqual(saved["payment_ref"], "AUTH-42")
        self.assertEqual(saved["created_at"], txn["created_at"])
        self.assertEqual(len(saved["lines"]), 1)
        line = saved["lines"][0]
        self.assertEqual(line["name"], "Milk Tea")
        self.assertEqual(line["qty"], 2)
        self.assertEqual(line["unit_price_cents"], 240)
        self.assertEqual(line["line_total_cents"], 480)

        listed = list_transactions(self.conn)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["receipt_no"], txn["receipt_no"])
        self.assertEqual(listed[0]["total_cents"], 480)

    def test_list_transactions_orders_newest_first(self):
        """The receipts list is ordered newest-first."""
        checkout(self.conn, [{"item_id": self.tea_id, "qty": 1}], "cash")
        checkout(self.conn, [{"item_id": self.cookie_id, "qty": 1}], "card")

        listed = list_transactions(self.conn)
        self.assertEqual(len(listed), 2)
        self.assertEqual(
            [t["receipt_no"] for t in listed],
            [_expected_no(2), _expected_no(1)],
        )

    def test_get_transaction_unknown_receipt_returns_none(self):
        """An unknown receipt number reads back as None (never an error)."""
        self.assertIsNone(get_transaction(self.conn, "1999-9999"))

    # ------------------------------------------------------- rejections (AC4)

    def test_rejections_write_nothing_and_do_not_advance_the_sequence(self):
        """Insufficient stock / unknown item / bad qty / unknown method write
        nothing: no transaction row, no stock change, no receipt number burned."""
        bad_charges = [
            # Insufficient stock.
            ([{"item_id": self.tea_id, "qty": 99}], "cash"),
            # Unknown item id.
            ([{"item_id": 9999, "qty": 1}], "cash"),
            # Non-positive quantity (zero, negative, non-integer).
            ([{"item_id": self.tea_id, "qty": 0}], "cash"),
            ([{"item_id": self.tea_id, "qty": -2}], "cash"),
            ([{"item_id": self.tea_id, "qty": "1"}], "cash"),
            # Unknown payment method.
            ([{"item_id": self.tea_id, "qty": 1}], "bitcoin"),
        ]

        for lines, method in bad_charges:
            with self.assertRaises(ValueError, msg="lines=%r method=%r" % (lines, method)):
                checkout(self.conn, lines, method)
            self.assertEqual(
                list_transactions(self.conn), [],
                msg="transaction written for lines=%r" % (lines,),
            )
            self.assertEqual(self._quantity(self.tea_id), 10, msg="lines=%r" % (lines,))
            self.assertEqual(self._quantity(self.cookie_id), 5, msg="lines=%r" % (lines,))

        # The receipt sequence must not have advanced: no row exists yet.
        seq = self.conn.execute("SELECT last_no FROM receipt_sequences").fetchone()
        self.assertIsNone(seq)

        # And the next valid charge still mints the first receipt of the year.
        txn = checkout(self.conn, [{"item_id": self.tea_id, "qty": 1}], "cash")
        self.assertEqual(txn["receipt_no"], _expected_no(1))

    # ------------------------------------------------------- concurrency (AC6)

    def test_concurrent_charges_mint_distinct_sequential_receipts(self):
        """Two connections charging at once get distinct, sequential receipt
        numbers and stock that never went negative."""
        item_id = add_item(self.conn, "Concurrent Tea", price_cents=300, quantity=10)
        self.conn.commit()

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def charge(qty):
            conn = init_db(self.db_path)
            try:
                barrier.wait(timeout=10)
                results.append(
                    checkout(conn, [{"item_id": item_id, "qty": qty}], "cash")
                )
            except ValueError as exc:
                errors.append(exc)
            finally:
                conn.close()

        t1 = threading.Thread(target=charge, args=(1,))
        t2 = threading.Thread(target=charge, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        self.assertFalse(t1.is_alive(), "charge thread 1 hung")
        self.assertFalse(t2.is_alive(), "charge thread 2 hung")
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)

        nums = sorted(result["receipt_no"] for result in results)
        self.assertEqual(len(set(nums)), 2, "receipt numbers must be distinct")
        self.assertEqual(nums, [_expected_no(1), _expected_no(2)])

        self.assertEqual(self._quantity(item_id), 8)  # 10 - 1 - 1, never negative

    def test_concurrent_oversell_is_rejected_and_stock_stays_non_negative(self):
        """Two connections racing to sell more than the stock: at most one may
        succeed, and the stock must never go negative."""
        item_id = add_item(self.conn, "Rare Item", price_cents=999, quantity=5)
        self.conn.commit()

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def charge(qty):
            conn = init_db(self.db_path)
            try:
                barrier.wait(timeout=10)
                results.append(
                    checkout(conn, [{"item_id": item_id, "qty": qty}], "cash")
                )
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                conn.close()

        t1 = threading.Thread(target=charge, args=(4,))
        t2 = threading.Thread(target=charge, args=(4,))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        self.assertFalse(t1.is_alive(), "charge thread 1 hung")
        self.assertFalse(t2.is_alive(), "charge thread 2 hung")

        # Demand is 4 + 4 = 8 but only 5 are in stock: exactly one sale may
        # succeed; the other is rejected by the atomic quantity guard.
        self.assertEqual(len(results), 1, "an over-sale must be rejected")
        self.assertEqual(len(errors), 1, "exactly one charge must be rejected")
        self.assertEqual(self._quantity(item_id), 1)  # 5 - 4, never negative


if __name__ == "__main__":
    unittest.main()