from __future__ import annotations

from datetime import UTC, datetime

import pytest

from restock_sentinel.db import Database
from restock_sentinel.models import StockCheckResult, TrackedProduct


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def product() -> TrackedProduct:
    return TrackedProduct(url="https://example.com/p/1", retailer="amazon_mx", sku="SKU1")


def test_upsert_product_is_idempotent_on_url(db, product):
    first_id = db.upsert_product(product)
    second_id = db.upsert_product(product)
    assert first_id == second_id
    assert len(db.list_products()) == 1


def test_upsert_product_updates_fields_on_conflict(db, product):
    product_id = db.upsert_product(product)
    updated = TrackedProduct(url=product.url, retailer="amazon_mx", nickname="New name")
    db.upsert_product(updated)

    rows = db.list_products()
    assert len(rows) == 1
    assert rows[0]["id"] == product_id
    assert rows[0]["nickname"] == "New name"


def test_record_and_read_stock_check(db, product):
    product_id = db.upsert_product(product)
    result = StockCheckResult(
        product=product,
        in_stock=True,
        price=19.99,
        title="Widget",
        checked_at=datetime.now(UTC),
    )
    db.record_stock_check(product_id, result)

    last = db.get_last_stock_check(product_id)
    assert last is not None
    assert bool(last["in_stock"]) is True
    assert last["price"] == 19.99


def test_was_previously_in_stock_false_with_no_history(db, product):
    product_id = db.upsert_product(product)
    assert db.was_previously_in_stock(product_id) is False


def test_was_previously_in_stock_reflects_latest_check(db, product):
    product_id = db.upsert_product(product)
    db.record_stock_check(
        product_id,
        StockCheckResult(product=product, in_stock=False, price=None, title=None),
    )
    db.record_stock_check(
        product_id,
        StockCheckResult(product=product, in_stock=True, price=9.99, title="Widget"),
    )
    assert db.was_previously_in_stock(product_id) is True


def test_log_alert_records_success_flag(db, product):
    product_id = db.upsert_product(product)
    db.log_alert(product_id, "discord", success=True, detail="ok")
    # No dedicated getter yet; this at least exercises the write path without error.
