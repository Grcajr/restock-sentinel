"""Tests for the Amazon.mx plugin's HTML-parsing logic.

Runs `_parse()` directly against saved HTML fixtures (no network/browser
needed) — including regression fixtures captured from a real live listing
during manual testing, covering the bot-check page, the async multi-seller
availability widget, and Amazon's "zero offers" signal.
"""

from __future__ import annotations

from pathlib import Path

from restock_sentinel.models import TrackedProduct
from restock_sentinel.plugins.amazon_mx import AmazonMXPlugin

FIXTURES = Path(__file__).parent / "fixtures"


def _product() -> TrackedProduct:
    return TrackedProduct(url="https://www.amazon.com.mx/dp/EXAMPLE", retailer="amazon_mx")


def test_parses_in_stock_page_with_price_and_title():
    html = (FIXTURES / "amazon_mx_in_stock.html").read_text()
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is True
    assert result.title == "Example Widget - Blue"
    assert result.price == 499.00
    assert result.error is None


def test_parses_out_of_stock_page():
    html = (FIXTURES / "amazon_mx_out_of_stock.html").read_text()
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is None


def test_missing_availability_block_reports_error_instead_of_false_positive():
    html = "<html><body><p>captcha challenge</p></body></html>"
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is not None


def test_in_stock_text_without_add_to_cart_button_is_treated_as_out_of_stock():
    html = """
    <html><body>
      <span id="productTitle">Widget</span>
      <div id="availability"><span>En stock.</span></div>
    </body></html>
    """
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False


def test_multi_seller_listing_with_populated_message_is_parsed_but_conservatively_out_of_stock():
    """Regression test for a real listing found during manual testing.

    Multi-seller/marketplace listings (common for TCG/collectibles) show a
    `.primary-availability-message` instead of the simple single-Buy-Box
    layout, and never have `#add-to-cart-button`/`#buy-now-button`. We can
    read the message text now (previously this returned `error`), but the
    button-based purchasability check still conservatively reports
    out-of-stock here — false negatives are the safer failure mode for a
    restock alert than false positives. See the plugin's module docstring.
    """
    html = (FIXTURES / "amazon_mx_multi_seller_populated.html").read_text()
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.raw_status == "Disponible. Cantidad:"
    assert result.error is None
    assert result.in_stock is False  # known conservative limitation, see docstring above


def test_multi_seller_listing_with_unpopulated_widget_reports_error():
    """The AOD widget can render with its message span still empty (async
    content that hasn't loaded yet) — that must degrade to an explicit
    error, never a false "in stock" or "out of stock" reading.
    """
    html = (FIXTURES / "amazon_mx_multi_seller_unpopulated.html").read_text()
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.error is not None


def test_multi_seller_listing_with_zero_offers_field_is_confirmed_out_of_stock():
    """Regression test for a real listing found during manual testing.

    Amazon exposes its own internal "any offers for sale?" signal as a
    hidden `#aod-has-oas-offers` field. When it's explicitly "false", an
    empty availability message means confirmed-out-of-stock (verified: the
    product genuinely had zero sellers offering it), not a parse failure —
    this should report cleanly, no `error` set.
    """
    html = (FIXTURES / "amazon_mx_multi_seller_zero_offers.html").read_text()
    plugin = AmazonMXPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is None
    assert "no offers" in result.raw_status.lower()
