"""Discord webhook alerting for restock events."""

from __future__ import annotations

import logging

import requests

from restock_sentinel.models import StockCheckResult

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10
_EMBED_COLOR_IN_STOCK = 0x57F287  # Discord "green"


class DiscordAlerter:
    """Sends restock notifications to a Discord channel via an incoming webhook."""

    def __init__(self, webhook_url: str, *, session: requests.Session | None = None):
        if not webhook_url:
            raise ValueError("webhook_url must be a non-empty Discord webhook URL")
        self.webhook_url = webhook_url
        self.session = session or requests.Session()

    def send_restock_alert(self, result: StockCheckResult) -> bool:
        """Post a restock embed to Discord. Returns True on a 2xx response."""
        payload = self._build_payload(result)
        try:
            response = self.session.post(
                self.webhook_url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Failed to send Discord alert for %s", result.product.url)
            return False

    @staticmethod
    def _build_payload(result: StockCheckResult) -> dict:
        title = result.title or result.product.display_name
        price_line = f"${result.price:,.2f}" if result.price is not None else "unknown"

        return {
            "username": "restock-sentinel",
            "embeds": [
                {
                    "title": f"🟢 Back in stock: {title}",
                    "url": result.product.url,
                    "color": _EMBED_COLOR_IN_STOCK,
                    "fields": [
                        {"name": "Retailer", "value": result.product.retailer, "inline": True},
                        {"name": "Price", "value": price_line, "inline": True},
                    ],
                    "timestamp": result.checked_at.isoformat(),
                }
            ],
        }
