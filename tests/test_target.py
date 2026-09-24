"""Tests for the Target plugin's HTML-parsing logic.

Runs `_parse()` directly against saved HTML fixtures (no network/browser
needed) -- including a regression fixture captured from a real live
listing during manual testing. That first live test disproved this
plugin's original assumption (that Target publishes schema.org JSON-LD like
many sites do -- it doesn't); see the plugin's module docstring for what
actually works instead. The in-stock fixture, by contrast, is still a
best-guess sample (the live test happened to hit a sold-out item) and
should be swapped for a real captured fixture once an in-stock listing is
tested live.
"""

from __future__ import annotations

from pathlib import Path

from restock_sentinel.models import TrackedProduct
from restock_sentinel.plugins.target import TargetPlugin

FIXTURES = Path(__file__).parent / "fixtures"


def _product() -> TrackedProduct:
    return TrackedProduct(url="https://www.target.com/p/example/-/A-12345678", retailer="target")


def test_parses_real_out_of_stock_listing_with_price_and_title():
    """Regression test for a real listing found during manual testing: a
    genuinely sold-out Pokemon TCG collection. Confirms the plugin reads
    Target's actual disabled-Add-to-Cart-button signal correctly.
    """
    html = (FIXTURES / "target_out_of_stock.html").read_text()
    plugin = TargetPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is None
    assert result.price == 19.99
    assert "Pok" in result.title  # exact accented spelling isn't the point here
    assert result.raw_status == "AddToCart.Disabled"


def test_parses_in_stock_page_with_price_and_title():
    """Uses a best-guess fixture (button without the disabled signal) --
    not yet confirmed against a real in-stock target.com page. See the
    module docstring's "known gap" note.
    """
    html = (FIXTURES / "target_in_stock.html").read_text()
    plugin = TargetPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is True
    assert result.title == "Example Gadget - Blue"
    assert result.price == 24.99
    assert result.error is None


def test_missing_cart_module_reports_error_instead_of_false_positive():
    """A page with no add-to-cart module at all (bot-check page, or a page
    Target has restructured) must degrade to an explicit error, never a
    guessed in-stock/out-of-stock reading.
    """
    html = (FIXTURES / "target_no_cart_module.html").read_text()
    plugin = TargetPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is not None
