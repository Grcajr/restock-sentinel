"""Target (target.com) — planned, not yet implemented.

TODO: Target exposes a semi-public "RedSky" JSON API
(`redsky.target.com/redsky_aggregations/...`) that its own site uses
client-side. Hitting that directly (with a valid `key` query param) is
likely more reliable than scraping rendered HTML, but the key rotates
periodically and needs to be captured from the site's own network traffic.
"""

from __future__ import annotations

from restock_sentinel.plugins._stub_template import UnimplementedPlugin
from restock_sentinel.registry import register_plugin


@register_plugin
class TargetPlugin(UnimplementedPlugin):
    name = "target"
