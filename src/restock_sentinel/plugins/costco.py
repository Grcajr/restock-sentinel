"""Costco (costco.com) — planned, not yet implemented.

TODO: Costco's stock/availability signal for warehouse-only items depends
on a selected warehouse (zip code), so ``TrackedProduct`` will need an
optional ``location`` field threaded through before this plugin can be
accurate for anything other than "ship it to me" items.
"""

from __future__ import annotations

from restock_sentinel.plugins._stub_template import UnimplementedPlugin
from restock_sentinel.registry import register_plugin


@register_plugin
class CostcoPlugin(UnimplementedPlugin):
    name = "costco"
