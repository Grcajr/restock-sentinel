"""Walmart (walmart.com / walmart.com.mx) — planned, not yet implemented.

TODO: Walmart's product pages are hydrated client-side from a JSON blob
embedded in a ``<script id="__NEXT_DATA__">`` tag rather than static HTML,
so this will need to parse that JSON (similar shape to Amazon's approach,
different extraction) instead of CSS-selecting rendered text. Availability
lives under `product.availabilityStatus` in that payload as of early 2026.
"""

from __future__ import annotations

from restock_sentinel.plugins._stub_template import UnimplementedPlugin
from restock_sentinel.registry import register_plugin


@register_plugin
class WalmartPlugin(UnimplementedPlugin):
    name = "walmart"
