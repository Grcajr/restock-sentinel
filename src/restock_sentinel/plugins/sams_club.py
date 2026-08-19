"""Sam's Club (samsclub.com) — planned, not yet implemented.

TODO: Sam's Club membership-gates most product/pricing data behind a login,
so this plugin will likely need an authenticated ``requests.Session``
(cookies from a logged-in session, refreshed periodically) rather than
anonymous GETs like the Amazon.mx plugin uses.
"""

from __future__ import annotations

from restock_sentinel.plugins._stub_template import UnimplementedPlugin
from restock_sentinel.registry import register_plugin


@register_plugin
class SamsClubPlugin(UnimplementedPlugin):
    name = "sams_club"
