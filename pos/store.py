"""SQLite repository for the POS backend.

Stores stock items, mints sequential receipt numbers, and records sales
atomically. Prices are integer cents to avoid float rounding.

Transaction discipline:
- Item writes (``add_item``) leave committing to the caller (the server
  layer), matching the baseline behaviour.
- ``checkout`` manages its own atomic ``with conn:`` transaction, so a
  failed sale rolls back and writes nothing.
"""

import sqlite3
from datetime import datetime

DEFAULT_DB_PATH = "stock.db"

# Allowed payment methods. Payments are recorded, not processed (per the
# PRD/design); each method takes an optional free-text reference.
PAYMENT_METHODS = frozenset({
    "cash",
    "card",
    "octopus",
    "fps",
    "alipayhk",
    "wechatpayhk",
    "payme",
    "store-credit",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0)
);

CREATE TABLE IF NOT EXISTS receipt_sequences (
    year    INTEGER PRIMARY KEY,
    last_no INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no     TEXT    NOT NULL UNIQUE,
    receipt_year   INTEGER NOT NULL,
    subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    total_cents    INTEGER NOT NULL CHECK (total_cents >= 0),
    payment_method TEXT    NOT NULL CHECK (payment_method IN ('cash', 'card', 'octopus', 'fps', 'alipayhk', 'wechatpayhk', 'payme', 'store-credit')),
    payment_ref    TEXT,
    created_at     TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id           INTEGER NOT NULL REFERENCES transactions(id),
    item_id          INTEGER NOT NULL REFERENCES items(id),
    name             TEXT    NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    qty              INTEGER NOT NULL CHECK (qty > 0),
    line_total_cents INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_lines_txn ON transaction_lines(txn_id);
"""


def init_db(db_path=DEFAULT_DB_PATH):
    """Open (creating if needed) the database and ensure the schema exists.

    Returns a connection with ``row_factory`` set to ``sqlite3.Row`` and the
    per-connection PRAGMAs applied (WAL journal, foreign keys on, a 5s busy
    timeout, and NORMAL synchronous mode) so concurrent stations can share
    the single SQLite file safely.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def list_items(conn):
    """Return all stock items as ``{id, name, price_cents, quantity}`` dicts.

    Items are ordered by ``id`` (insertion order).
    """
    rows = conn.execute(
        "SELECT id, name, price_cents, quantity FROM items ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def add_item(conn, name, price_cents, quantity=0):
    """Insert a stock item and return its new id.

    Validation (raises ``ValueError`` on any failure):
      - ``name``: trimmed, non-blank, unique case-insensitively.
      - ``price_cents``: integer >= 0.
      - ``quantity``: integer >= 0; ``None``/blank defaults to 0.

    The caller is responsible for committing the transaction.
    """
    name = _clean_name(name)
    price_cents = _non_negative_int(price_cents, "price")
    if quantity is None:
        quantity = 0
    quantity = _non_negative_int(quantity, "quantity")

    if _name_exists(conn, name):
        raise ValueError("An item with this name already exists.")

    try:
        cursor = conn.execute(
            "INSERT INTO items (name, price_cents, quantity) VALUES (?, ?, ?)",
            (name, price_cents, quantity),
        )
    except sqlite3.IntegrityError as exc:
        # Fallback for a concurrent duplicate insert; the UNIQUE constraint
        # (COLLATE NOCASE) is the source of truth.
        raise ValueError("An item with this name already exists.") from exc
    return cursor.lastrowid


def checkout(conn, lines, payment_method, payment_ref=None):
    """Record a sale atomically and return the sale dict.

    ``lines`` is a list of ``{"item_id": int, "qty": int}`` mappings;
    ``payment_method`` must be one of :data:`PAYMENT_METHODS`;
    ``payment_ref`` is an optional free-text reference.

    The whole sale runs inside one ``with conn:`` transaction. On any failure
    (an empty sale, an unknown item, a non-positive quantity, insufficient
    stock, or an unknown payment method) it raises ``ValueError`` and rolls
    back, so nothing is written.

    On success it:
      1. atomically check-and-decrements stock per line with
         ``UPDATE items SET quantity = quantity - ? WHERE id = ? AND
         quantity >= ?``,
      2. mints the next sequential receipt number for the current calendar
         year via ``receipt_sequences`` (``"{year}-{no:04d}"``),
      3. inserts the ``transactions`` row and its ``transaction_lines``,
         snapshotting name/unit price and computing the subtotal/total
         server-side, and
      4. returns ``{receipt_no, receipt_year, subtotal_cents, total_cents,
         payment_method, payment_ref, created_at, lines}``.
    """
    with conn:
        if payment_method not in PAYMENT_METHODS:
            raise ValueError("Unknown payment method: {!r}.".format(payment_method))

        if not lines:
            raise ValueError("A sale must contain at least one line.")

        # Normalise the payment reference once, before any writes.
        ref = str(payment_ref).strip() if payment_ref is not None else None
        ref = ref or None

        # Validate every line and snapshot the item rows (name/unit price)
        # so receipts survive later item edits.
        prepared = []
        for line in lines:
            if not isinstance(line, dict):
                raise ValueError("Each sale line must be an {item_id, qty} mapping.")
            item_id = line.get("item_id")
            qty = line.get("qty")
            if isinstance(item_id, bool) or not isinstance(item_id, int):
                raise ValueError("Unknown item with id {!r}.".format(item_id))
            if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
                raise ValueError(
                    "Quantity for item {} must be a positive integer.".format(item_id)
                )
            item = conn.execute(
                "SELECT name, price_cents FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                raise ValueError("Unknown item with id {}.".format(item_id))
            prepared.append((item_id, qty, item["name"], item["price_cents"]))

        # Atomic check-and-decrement per line; stock can never go negative.
        for item_id, qty, name, _unit_price_cents in prepared:
            cursor = conn.execute(
                "UPDATE items SET quantity = quantity - ? "
                "WHERE id = ? AND quantity >= ?",
                (qty, item_id, qty),
            )
            if cursor.rowcount == 0:
                raise ValueError("Insufficient stock for item {}.".format(name))

        # Mint the next sequential receipt number for this calendar year.
        year = datetime.now().year
        conn.execute(
            "INSERT OR IGNORE INTO receipt_sequences (year, last_no) VALUES (?, 0)",
            (year,),
        )
        conn.execute(
            "UPDATE receipt_sequences SET last_no = last_no + 1 WHERE year = ?",
            (year,),
        )
        sequence = conn.execute(
            "SELECT last_no FROM receipt_sequences WHERE year = ?", (year,)
        ).fetchone()
        receipt_no = "{}-{:04d}".format(year, sequence["last_no"])

        subtotal_cents = sum(
            unit_price_cents * qty
            for _item_id, qty, _name, unit_price_cents in prepared
        )
        total_cents = subtotal_cents  # no discounts this cycle

        cursor = conn.execute(
            "INSERT INTO transactions "
            "(receipt_no, receipt_year, subtotal_cents, total_cents, "
            " payment_method, payment_ref) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (receipt_no, year, subtotal_cents, total_cents, payment_method, ref),
        )
        txn_id = cursor.lastrowid

        sale_lines = []
        for item_id, qty, name, unit_price_cents in prepared:
            line_total_cents = unit_price_cents * qty
            conn.execute(
                "INSERT INTO transaction_lines "
                "(txn_id, item_id, name, unit_price_cents, qty, line_total_cents) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (txn_id, item_id, name, unit_price_cents, qty, line_total_cents),
            )
            sale_lines.append({
                "item_id": item_id,
                "name": name,
                "unit_price_cents": unit_price_cents,
                "qty": qty,
                "line_total_cents": line_total_cents,
            })

        created_at = conn.execute(
            "SELECT created_at FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()["created_at"]

        return {
            "receipt_no": receipt_no,
            "receipt_year": year,
            "subtotal_cents": subtotal_cents,
            "total_cents": total_cents,
            "payment_method": payment_method,
            "payment_ref": ref,
            "created_at": created_at,
            "lines": sale_lines,
        }


def get_transaction(conn, receipt_no):
    """Return one sale (transaction + lines) keyed by receipt number.

    Returns ``None`` when no transaction matches ``receipt_no``. The dict
    has the same shape as :func:`checkout`'s return value.
    """
    row = conn.execute(
        "SELECT receipt_no, receipt_year, subtotal_cents, total_cents, "
        "payment_method, payment_ref, created_at "
        "FROM transactions WHERE receipt_no = ?",
        (receipt_no,),
    ).fetchone()
    if row is None:
        return None
    txn = dict(row)
    lines = conn.execute(
        "SELECT item_id, name, unit_price_cents, qty, line_total_cents "
        "FROM transaction_lines WHERE txn_id = "
        "(SELECT id FROM transactions WHERE receipt_no = ?) ORDER BY id",
        (receipt_no,),
    ).fetchall()
    txn["lines"] = [dict(line) for line in lines]
    return txn


def list_transactions(conn):
    """List past sales as ``{receipt_no, created_at, total_cents}`` dicts.

    Ordered newest-first by insertion order.
    """
    rows = conn.execute(
        "SELECT receipt_no, created_at, total_cents "
        "FROM transactions ORDER BY id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def _clean_name(name):
    if not isinstance(name, str):
        raise ValueError("Item name must be text.")
    name = name.strip()
    if not name:
        raise ValueError("Item name must not be blank.")
    return name


def _non_negative_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be a non-negative integer." % field)
    if value < 0:
        raise ValueError("%s must be a non-negative integer." % field)
    return value


def _name_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM items WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
    ).fetchone()
    return row is not None