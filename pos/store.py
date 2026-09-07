"""SQLite repository for stock items and the stock-movement ledger.

Part of the tiny POS system. Items are stored in a SQLite database with
prices in integer cents to avoid float rounding. Callers own the transaction
for ``add_item`` (it performs the INSERT without committing so the server
layer can commit or roll back as needed); ``adjust_quantity`` manages its
own transaction with ``with conn:`` and is atomic regardless of the caller.
"""

import sqlite3

DEFAULT_DB_PATH = "stock.db"

# Per-connection PRAGMAs applied in ``init_db`` before the DDL block. WAL
# journaling plus a busy timeout keep concurrent read/write requests from
# failing with ``database is locked``; foreign keys are enforced so ledger
# rows cannot dangle; ``synchronous=NORMAL`` is the WAL-recommended durability
# trade-off.
_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0)
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    delta         INTEGER NOT NULL CHECK (delta != 0),
    movement_type TEXT    NOT NULL CHECK (movement_type IN ('in','out')),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_movements_item
    ON stock_movements(item_id);
"""


class ItemNotFoundError(ValueError):
    """Raised when an operation references an item id that does not exist.

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working, while the HTTP layer can map it to a 404 without sniffing the
    message.
    """


def init_db(db_path=DEFAULT_DB_PATH):
    """Open (creating if needed) the database and ensure the schema exists.

    Applies the per-connection concurrency PRAGMAs (WAL, foreign keys, busy
    timeout, synchronous) before creating the schema. Returns a connection
    with ``row_factory`` set to ``sqlite3.Row``.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
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


def adjust_quantity(conn, item_id, delta, movement_type):
    """Record a stock movement and update the item's on-hand quantity.

    ``delta`` is signed: positive for a stock ``in``, negative for a stock
    ``out``, and its sign must match ``movement_type``. Validates, in order:

      - ``movement_type`` is ``'in'`` or ``'out'`` (else ``ValueError``);
      - ``delta`` is a non-zero int (bool excluded) (else ``ValueError``);
      - the sign of ``delta`` matches ``movement_type`` (else ``ValueError``);
      - the item exists (else ``ItemNotFoundError``);
      - the resulting on-hand is non-negative (else ``ValueError``).

    On success it inserts the movement row and updates ``items.quantity``
    inside one ``with conn:`` transaction (auto-commit on success, auto-rollback
    on any exception) and returns the new on-hand int.
    """
    if movement_type not in ("in", "out"):
        raise ValueError(
            "movement_type must be 'in' or 'out', got {!r}.".format(movement_type)
        )
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise ValueError("delta must be a non-zero integer, got {!r}.".format(delta))
    if delta == 0:
        raise ValueError("delta must be a non-zero integer.")
    if movement_type == "in" and delta < 0:
        raise ValueError("An 'in' movement must have a positive delta.")
    if movement_type == "out" and delta > 0:
        raise ValueError("An 'out' movement must have a negative delta.")

    current = conn.execute(
        "SELECT quantity FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if current is None:
        raise ItemNotFoundError("Item {!r} does not exist.".format(item_id))

    new_quantity = current["quantity"] + delta
    if new_quantity < 0:
        raise ValueError(
            "Adjustment would make on-hand quantity negative ({}).".format(new_quantity)
        )

    with conn:
        conn.execute(
            "INSERT INTO stock_movements (item_id, delta, movement_type) "
            "VALUES (?, ?, ?)",
            (item_id, delta, movement_type),
        )
        conn.execute(
            "UPDATE items SET quantity = quantity + ? WHERE id = ?",
            (delta, item_id),
        )
    return new_quantity


def list_movements(conn, item_id=None):
    """Return the append-only movement ledger as ``dict`` rows.

    Rows are ``{id, item_id, delta, movement_type, created_at}`` ordered by
    ``id`` ascending; optionally filtered to a single ``item_id``. Read-only
    audit helper (no ledger UI this cycle).
    """
    if item_id is None:
        rows = conn.execute(
            "SELECT id, item_id, delta, movement_type, created_at "
            "FROM stock_movements ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item_id, delta, movement_type, created_at "
            "FROM stock_movements WHERE item_id = ? ORDER BY id",
            (item_id,),
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