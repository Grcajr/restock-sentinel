"""Pokémon Center (pokemoncenter.com) stock-check plugin.

The original stub flagged that Pokémon Center runs an aggressive
bot-detection layer (PerimeterX) that blocks plain HTTP-client traffic
outright, especially on high-demand items -- so, like amazon_mx, target,
and walmart before it, this fetches pages with a real headless browser
(Playwright) rather than ``requests``. That's what avoids triggering the
bot-check in the first place; it is not a way of getting past it. If a page
ever renders an explicit bot-check/CAPTCHA interstitial instead of the
product, this plugin does not click through or otherwise automate past it
-- a result with ``error`` set and a saved debug snapshot is the correct,
honest outcome. See the amazon_mx plugin's module docstring for the fuller
reasoning behind that boundary; it applies here unchanged.

Unlike Target (which turned out to carry no structured data at all) or
Walmart (a Next.js `__NEXT_DATA__` blob), Pokémon Center's storefront
platform commonly publishes standard schema.org JSON-LD ``Product``/
``Offer`` data for SEO -- the same shape amazon_mx's first (later
corrected) attempt assumed Amazon had. Rather than assume that holds here
too without checking, this plugin tries JSON-LD first and, if it's missing
or unusable, falls back to scanning the rendered DOM for an add-to-cart /
sold-out signal by button text and ``disabled`` state -- closer to how the
Target plugin ended up working. Whichever path finds usable data wins;
if neither does, that's an ``error``, never a guess.

NOT yet verified against a live pokemoncenter.com page -- this project's
sandbox can't reach pokemoncenter.com directly, same limitation as
walmart.py. Treat the first live run as a real test; both the JSON-LD path
and the DOM-fallback path are best guesses based on common patterns for
this kind of storefront, not confirmed selectors. Expect at least one round
of fixes, the same way amazon_mx, target, and walmart all needed one.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from restock_sentinel.models import StockCheckResult, TrackedProduct
from restock_sentinel.plugins.base import DEFAULT_USER_AGENT, RetailerPlugin
from restock_sentinel.registry import register_plugin

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path("data/debug")
_PRICE_RE = re.compile(r"[\d,]+\.?\d*")

# Button/link text that signals "not purchasable right now" when no JSON-LD
# availability field is present. Matched case-insensitively against the
# element's own text.
_OUT_OF_STOCK_PHRASES = ("sold out", "notify me", "out of stock", "coming soon")
_IN_STOCK_PHRASES = ("add to cart", "add to bag")


@register_plugin
class PokemonCenterPlugin(RetailerPlugin):
    name = "pokemon_center"

    PAGE_LOAD_TIMEOUT_SECONDS = 20

    def check_stock(self, product: TrackedProduct) -> StockCheckResult:
        try:
            html, status_code, final_url = self._fetch_rendered_html(product.url)
        except PlaywrightError as exc:
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=None,
                title=None,
                error=f"browser fetch failed: {exc}",
            )

        logger.debug(
            "pokemon_center GET(rendered) %s -> status=%s bytes=%d",
            product.url,
            status_code,
            len(html),
        )

        result = self._parse(product, html)
        if result.error:
            self._save_debug_snapshot(html, status_code, final_url)
        return result

    def _fetch_rendered_html(self, url: str) -> tuple[str, int | None, str]:
        """Load ``url`` in a real (headless) Chromium tab and return its HTML.

        Mirrors amazon_mx/target/walmart: a real browser is what avoids
        triggering Pokémon Center's PerimeterX bot-detection in the first
        place, rather than something that defeats it. See the module
        docstring.
        """
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT, locale="en-US")
                response = page.goto(
                    url,
                    timeout=self.PAGE_LOAD_TIMEOUT_SECONDS * 1000,
                    wait_until="domcontentloaded",
                )
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightError:
                    pass  # fine if it never goes fully idle; we still read what we have
                html = page.content()
                status_code = response.status if response else None
                return html, status_code, page.url
            finally:
                browser.close()

    @staticmethod
    def _save_debug_snapshot(html: str, status_code: int | None, final_url: str) -> None:
        """Best-effort dump of the last failing response, for triage.

        Never raises -- a failure here should never mask the real error from
        the stock check itself.
        """
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (_DEBUG_DIR / "pokemon_center_last_failure.html").write_text(html, encoding="utf-8")
            (_DEBUG_DIR / "pokemon_center_last_failure_meta.txt").write_text(
                f"status_code: {status_code}\n"
                f"final_url: {final_url}\n"
                f"content_length: {len(html)}\n",
                encoding="utf-8",
            )
            logger.info("Saved failing response to %s for inspection", _DEBUG_DIR)
        except OSError:
            logger.debug("Could not write debug snapshot", exc_info=True)

    def _parse(self, product: TrackedProduct, html: str) -> StockCheckResult:
        soup = BeautifulSoup(html, "html.parser")

        json_ld_result = self._parse_from_json_ld(product, soup)
        if json_ld_result is not None:
            return json_ld_result

        return self._parse_from_dom(product, soup)

    # -- Path 1: schema.org JSON-LD, if the page publishes it -----------

    def _parse_from_json_ld(
        self, product: TrackedProduct, soup: BeautifulSoup
    ) -> StockCheckResult | None:
        """Return a result built from a JSON-LD ``Product`` block, or None
        if no usable one is found (falls through to the DOM path)."""
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except (json.JSONDecodeError, TypeError):
                continue

            product_node = self._find_product_node(data)
            if product_node is None:
                continue

            offer = self._first_offer(product_node.get("offers"))
            if offer is None:
                continue

            availability = str(offer.get("availability", ""))
            price = self._coerce_price(offer.get("price"))
            title = product_node.get("name")

            # A schema.org availability URL looks like
            # "https://schema.org/InStock" or "http://schema.org/OutOfStock"
            # -- match on the trailing segment, not the whole URL, so http
            # vs https doesn't matter.
            in_stock = availability.rsplit("/", 1)[-1].lower() == "instock"

            return StockCheckResult(
                product=product,
                in_stock=in_stock,
                price=price,
                title=title,
                raw_status=availability or None,
            )
        return None

    @staticmethod
    def _find_product_node(data: Any) -> dict | None:
        """Find a dict that looks like a schema.org Product, handling the
        common wrapper shapes (`@graph` lists, plain lists, single dict)."""
        candidates: list[Any] = []
        if isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        elif isinstance(data, list):
            candidates = data
        else:
            candidates = [data]

        for node in candidates:
            if isinstance(node, dict) and str(node.get("@type", "")).lower() == "product":
                return node
        return None

    @staticmethod
    def _first_offer(offers: Any) -> dict | None:
        if isinstance(offers, dict):
            return offers
        if isinstance(offers, list) and offers and isinstance(offers[0], dict):
            return offers[0]
        return None

    @staticmethod
    def _coerce_price(raw_price: Any) -> float | None:
        if raw_price is None:
            return None
        try:
            return float(raw_price)
        except (TypeError, ValueError):
            match = _PRICE_RE.search(str(raw_price))
            return float(match.group(0).replace(",", "")) if match else None

    # -- Path 2: fall back to the rendered DOM ---------------------------

    def _parse_from_dom(self, product: TrackedProduct, soup: BeautifulSoup) -> StockCheckResult:
        title = self._extract_title(soup)
        price = self._extract_price(soup)

        button = self._find_purchase_button(soup)
        if button is None:
            # Could be a bot-check interstitial, or the page layout has
            # changed -- degrade honestly rather than guess, same as
            # target.py's missing-cart-module case.
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=price,
                title=title,
                error=(
                    "could not find a JSON-LD product block or a recognizable "
                    "add-to-cart/sold-out button -- likely a bot-check "
                    "interstitial, or Pokémon Center has changed this page's layout"
                ),
            )

        button_text = button.get_text(strip=True).lower()
        is_disabled = button.has_attr("disabled")
        looks_out_of_stock = is_disabled or any(
            phrase in button_text for phrase in _OUT_OF_STOCK_PHRASES
        )

        return StockCheckResult(
            product=product,
            in_stock=not looks_out_of_stock,
            price=price,
            title=title,
            raw_status=button.get_text(strip=True) or None,
        )

    @staticmethod
    def _find_purchase_button(soup: BeautifulSoup) -> Any | None:
        for element in soup.find_all(("button", "a")):
            text = element.get_text(strip=True).lower()
            if not text:
                continue
            if any(phrase in text for phrase in _IN_STOCK_PHRASES) or any(
                phrase in text for phrase in _OUT_OF_STOCK_PHRASES
            ):
                return element
        return None

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str | None:
        node = soup.select_one("h1")
        return node.get_text(strip=True) if node else None

    @staticmethod
    def _extract_price(soup: BeautifulSoup) -> float | None:
        price_selectors = (
            '[data-testid="product-price"]',
            ".product-price",
            ".price",
        )
        for selector in price_selectors:
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
