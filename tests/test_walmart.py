"""Tests for the Walmart plugin's __NEXT_DATA__-parsing logic.

Runs `_parse()` directly against sample HTML fixtures (no network/browser
needed). These are hand-built sample payloads matching the shape publicly
documented for Walmart's Next.js item pages -- NOT captured from a real
live listing, since this project's sandbox can't reach walmart.com. They
prove the recursive product-node search works correctly against a
realistically-nested payload; they do not prove Walmart's actual site still
looks like this. Expect to add a real regression fixture here after the
first live test, the same pattern amazon_mx and target both followed.
"""

from __future__ import annotations

from pathlib import Path

from restock_sentinel.models import TrackedProduct
from restock_sentinel.plugins.walmart import WalmartPlugin

FIXTURES = Path(__file__).parent / "fixtures"


def _product() -> TrackedProduct:
    return TrackedProduct(url="https://www.walmart.com/ip/example/123456789", retailer="walmart")


def test_parses_in_stock_page_with_price_and_title():
    html = (FIXTURES / "walmart_in_stock.html").read_text(encoding="utf-8")
    plugin = WalmartPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is True
    assert result.title == "Example Gadget - Blue"
    assert result.price == 24.99
    assert result.error is None


def test_parses_out_of_stock_page():
    html = (FIXTURES / "walmart_out_of_stock.html").read_text(encoding="utf-8")
    plugin = WalmartPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is None
    assert result.raw_status == "OUT_OF_STOCK"


def test_missing_next_data_reports_error_instead_of_false_positive():
    """A page with no __NEXT_DATA__ script at all (bot-check page, or a page
    Walmart has restructured) must degrade to an explicit error, never a
    guessed in-stock/out-of-stock reading.
    """
    html = (FIXTURES / "walmart_no_next_data.html").read_text(encoding="utf-8")
    plugin = WalmartPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is not None


def test_finds_product_node_nested_several_levels_deep():
    """The fixture buries the product record under
    props.pageProps.initialData.data.product -- five levels deep. This
    proves the recursive search isn't just checking the top level or one
    hardcoded path.
    """
    html = (FIXTURES / "walmart_in_stock.html").read_text(encoding="utf-8")
    plugin = WalmartPlugin()

    result = plugin._parse(_product(), html)

    assert result.title is not None
    assert result.price is not None


def test_find_product_node_returns_none_for_unrelated_json():
    """A JSON tree with no name+availability combination anywhere in it
    should report "not found" rather than matching something incidental.
    """
    plugin = WalmartPlugin()
    unrelated = {"props": {"pageProps": {"someOtherThing": {"foo": "bar", "count": 3}}}}

    assert plugin._find_product_node(unrelated) is None
