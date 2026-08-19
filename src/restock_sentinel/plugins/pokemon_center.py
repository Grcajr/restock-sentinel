"""Pokémon Center (pokemoncenter.com) — planned, not yet implemented.

TODO: Pokémon Center runs an aggressive bot-detection layer (PerimeterX)
that blocks plain ``requests`` traffic outright on high-demand items. This
one will probably need to reuse the Playwright browser context from
:mod:`restock_sentinel.checkout` for the stock check itself, not just the
checkout step, unlike every other plugin here.
"""

from __future__ import annotations

from restock_sentinel.plugins._stub_template import UnimplementedPlugin
from restock_sentinel.registry import register_plugin


@register_plugin
class PokemonCenterPlugin(UnimplementedPlugin):
    name = "pokemon_center"
