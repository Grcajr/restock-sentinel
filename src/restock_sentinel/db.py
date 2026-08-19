"""SQLite persistence: tracked products, stock-check history, alert log.

Kept deliberately dependency-free (stdlib ``sqlite3`` only) so the project
has no external database to stand up — it's a single file on disk.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from restock_sentinel.models import StockCheckResult, TrackedProduct

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    retailer TEXT NOT NULL,
    sku TEXT,
    nickname TEXT,
    max_price REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    in_stock INTEGER NOT NULL,
    price REAL,
    title TEXT,
    raw_status TEXT,
    error TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    success INTEGER NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_stock_checks_product_time
    ON stock_checks(product_id, checked_at);
"""


class Database:
    """Thin, explicit wrapper around a SQLite connection.

    Not an ORM on purpose — for a project this size, plain SQL keeps the
    query behaviour obvious and easy to reason about (and to explain in an
    interview).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._cursor() as cur:
            cur.executescript(SCHEMA)

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- products ----------------------------------------------------------

    def upsert_product(self, product: TrackedProduct) -> int:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (url, retailer, sku, nickname, max_price)
                VALUES (:url, :retailer, :sku, :nickname, :max_price)
                ON CONFLICT(url) DO UPDATE SET
                    retailer = excluded.retailer,
                    sku = excluded.sku,
                    nickname = excluded.nickname,
                    max_price = excluded.max_price
                """,
                {
                    "url": product.url,
                    "retailer": product.retailer,
                    "sku": product.sku,
                    "nickname": product.nickname,
                    "max_price": product.max_price,
                },
            )
            row = cur.execute(
                "SELECT id FROM products WHERE url = ?", (product.url,)
            ).fetchone()
            return int(row["id"])

    def list_products(self) -> list[sqlite3.Row]:
        with self._cursor() as cur:
            return cur.execute("SELECT * FROM products ORDER BY id").fetchall()

    # -- stock checks --------------------------------------------------------

    def record_stock_check(self, product_id: int, result: StockCheckResult) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO stock_checks
                    (product_id, in_stock, price, title, raw_status, error, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    int(result.in_stock),
                    result.price,
                    result.title,
                    result.raw_status,
                    result.error,
                    result.checked_at.isoformat(),
                ),
            )

    def get_last_stock_check(self, product_id: int) -> sqlite3.Row | None:
        with self._cursor() as cur:
            return cur.execute(
                """
                SELECT * FROM stock_checks
                WHERE product_id = ?
                ORDER BY checked_at DESC
                LIMIT 1
                """,
                (product_id,),
            ).fetchone()

    def was_previously_in_stock(self, product_id: int) -> bool:
        last = self.get_last_stock_check(product_id)
        return bool(last["in_stock"]) if last else False

    # -- alerts --------------------------------------------------------------

    def log_alert(
        self, product_id: int, channel: str, success: bool, detail: str = ""
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (product_id, channel, success, detail)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, channel, int(success), detail),
            )
