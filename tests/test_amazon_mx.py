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
