"""SQLite repository for stock items.

Part of the tiny POS system. Items are stored in a SQLite database with
prices in integer cents to avoid float rounding. The caller owns the
transaction: ``add_item`` performs the INSERT without committing so the
server layer can commit or roll back as needed.
"""

import sqlite3

DEFAULT_DB_PATH = "stock.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0)
);
"""


def init_db(db_path=DEFAULT_DB_PATH):
    """Open (creating if needed) the database and ensure the schema exists.

    Returns a connection with ``row_factory`` set to ``sqlite3.Row``.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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