"""Tests for the Pokémon Center plugin's HTML-parsing logic.

Runs `_parse()` directly against saved HTML fixtures (no network/browser
needed). All fixtures here are synthetic best-guesses -- this plugin has
not yet been live-tested against a real pokemoncenter.com page (the
project's sandbox can't reach it directly, same limitation as walmart.py).
See the plugin's module docstring for the two parsing paths this covers:
schema.org JSON-LD first, falling back to a DOM add-to-cart/sold-out
button scan when JSON-LD isn't present or usable.
"""

from __future__ import annotations

from pathlib import Path

from restock_sentinel.models import TrackedProduct
from restock_sentinel.plugins.pokemon_center import PokemonCenterPlugin

FIXTURES = Path(__file__).parent / "fixtures"


def _product() -> TrackedProduct:
    return TrackedProduct(
        url="https://www.pokemoncenter.com/product/example-12345", retailer="pokemon_center"
    )


def test_parses_json_ld_in_stock_listing():
    html = (FIXTURES / "pokemon_center_json_ld_in_stock.html").read_text(encoding="utf-8")
    plugin = PokemonCenterPlugin()

    result = plugin._parse(_product(), html)

    assert result.error is None
    assert result.in_stock is True
    assert result.price == 24.99
    assert result.title == "Pikachu Plush - 8 In."
    assert result.raw_status == "https://schema.org/InStock"


def test_parses_json_ld_out_of_stock_listing():
    """Confirms the plugin actually reads the availability URL rather than
    treating "JSON-LD found" as automatically in-stock -- also covers the
    http:// (vs https://) schema.org URL variant.
    """
    html = (FIXTURES / "pokemon_center_json_ld_out_of_stock.html").read_text(encoding="utf-8")
    plugin = PokemonCenterPlugin()

    result = plugin._parse(_product(), html)

    assert result.error is None
    assert result.in_stock is False
    assert result.price == 49.99
    assert result.title == "Pokémon TCG: 30th Celebration Elite Trainer Box"
    assert result.raw_status == "http://schema.org/OutOfStock"


def test_falls_back_to_dom_when_no_json_ld_present_out_of_stock():
    html = (FIXTURES / "pokemon_center_dom_fallback_sold_out.html").read_text(encoding="utf-8")
    plugin = PokemonCenterPlugin()

    result = plugin._parse(_product(), html)

    assert result.error is None
    assert result.in_stock is False
    assert result.price == 19.99
    assert result.title == "Charizard V Battle Deck"
    assert result.raw_status == "Sold Out"


def test_falls_back_to_dom_when_no_json_ld_present_in_stock():
    html = (FIXTURES / "pokemon_center_dom_fallback_in_stock.html").read_text(encoding="utf-8")
    plugin = PokemonCenterPlugin()

    result = plugin._parse(_product(), html)

    assert result.error is None
    assert result.in_stock is True
    assert result.price == 22.99
    assert result.title == "Eevee Plush - 8 In."


def test_unrecognized_page_reports_error_instead_of_false_positive():
    """A page with neither JSON-LD nor a recognizable purchase button
    (bot-check interstitial, or a restructured layout) must degrade to an
    explicit error, never a guessed in-stock/out-of-stock reading.
    """
    html = (FIXTURES / "pokemon_center_unrecognized.html").read_text(encoding="utf-8")
    plugin = PokemonCenterPlugin()

    result = plugin._parse(_product(), html)

    assert result.in_stock is False
    assert result.error is not None
