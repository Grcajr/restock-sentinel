"""Amazon Mexico (amazon.com.mx) stock-check plugin.

Amazon renders product pages server-side, so a plain HTTP GET + HTML parse
is enough to read availability and price — no headless browser required for
*checking* stock (Playwright is reserved for the actual dry-run checkout
flow in :mod:`restock_sentinel.checkout`, where real interaction is needed).

Notes on fragility, worth calling out explicitly: Amazon changes markup and
serves different layouts based on A/B tests, locale, and bot-detection
heuristics. The selectors below are deliberately layered (several fallbacks
per field) and every parse failure degrades to ``error`` rather than a
crash or a false "in stock". Treat this as a best-effort integration, not a
guarantee — that's true of scraping-based stock checks in general.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from restock_sentinel.models import StockCheckResult, TrackedProduct
from restock_sentinel.plugins.base import RetailerPlugin
from restock_sentinel.registry import register_plugin

_PRICE_RE = re.compile(r"[\d,]+\.?\d*")

# Phrases Amazon.mx uses on the availability line when something is NOT
# purchasable. Matched case-insensitively against the parsed text.
_OUT_OF_STOCK_PHRASES = (
    "no disponible",
    "actualmente no disponible",
    "no en existencia",
    "currently unavailable",
    "out of stock",
    "temporarily out of stock",
)


@register_plugin
class AmazonMXPlugin(RetailerPlugin):
    name = "amazon_mx"

    REQUEST_TIMEOUT_SECONDS = 15

    def check_stock(self, product: TrackedProduct) -> StockCheckResult:
        try:
            response = self.session.get(
                product.url, timeout=self.REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=None,
                title=None,
                error=f"request failed: {exc}",
            )

        return self._parse(product, response.text)

    def _parse(self, product: TrackedProduct, html: str) -> StockCheckResult:
        soup = BeautifulSoup(html, "html.parser")

        title = self._extract_title(soup)
        availability_text = self._extract_availability_text(soup)
        price = self._extract_price(soup)

        if availability_text is None:
            # No availability block at all usually means we got a CAPTCHA /
            # bot-check page rather than the real product page.
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=price,
                title=title,
                raw_status=None,
                error="could not locate availability block (possible bot check page)",
            )

        normalized = availability_text.strip().lower()
        in_stock = not any(phrase in normalized for phrase in _OUT_OF_STOCK_PHRASES)
        # A missing "Add to Cart" button is also a strong out-of-stock signal
        # even when the availability text looks ambiguous.
        if in_stock and soup.select_one("#add-to-cart-button, #buy-now-button") is None:
            in_stock = False

        return StockCheckResult(
            product=product,
            in_stock=in_stock,
            price=price,
            title=title,
            raw_status=availability_text.strip(),
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str | None:
        node = soup.select_one("#productTitle")
        return node.get_text(strip=True) if node else None

    @staticmethod
    def _extract_availability_text(soup: BeautifulSoup) -> str | None:
        for selector in ("#availability .a-declarative", "#availability span", "#availability"):
            node = soup.select_one(selector)
            if node and node.get_text(strip=True):
                return node.get_text(strip=True)
        return None

    @staticmethod
    def _extract_price(soup: BeautifulSoup) -> float | None:
        candidates = [
            "span.a-price span.a-offscreen",
            "#corePrice_feature_div span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
        ]
        for selector in candidates:
            node = soup.select_one(selector)
            if not node:
                continue
            match = _PRICE_RE.search(node.get_text(strip=True))
            if match:
                try:
                    return float(match.group(0).replace(",", ""))
                except ValueError:
                    continue
        return None
