"""Walmart (walmart.com) stock-check plugin.

Walmart's product pages are a Next.js app: the actual product data (name,
price, availability) isn't in static HTML, it's embedded as JSON inside a
``<script id="__NEXT_DATA__" type="application/json">`` tag and rendered
client-side from there. That much is a known, long-standing pattern for
Walmart's site (and is why the original stub for this plugin already noted
it). What's NOT yet confirmed is the *exact* nested path to the product
object within that JSON -- large Next.js apps commonly bury it several
layers deep under page-specific keys that shift between redesigns, and (as
the Target plugin's first attempt found the hard way) a guessed exact path
can simply be wrong.

To hedge against that, this plugin doesn't hardcode one exact key path.
Instead it walks the entire parsed JSON tree looking for the first
dictionary that looks like a product record: something with a name/title
field alongside an availability-style field. That's more resilient to the
data shifting a level deeper or shallower than expected, at the cost of
being a bit more code than a single dict lookup.

Like the other plugins, pages are fetched with a real headless browser
(Playwright), not a plain HTTP client -- large retailers commonly run
bot-detection that blocks simple scripted requests, and a real browser is
what avoids triggering that in the first place. If a page ever renders an
explicit bot-check/CAPTCHA interstitial instead of the product, this plugin
does not attempt to click through or solve it: a result with ``error`` set
and a saved debug snapshot is the correct, honest outcome. See the
amazon_mx plugin's module docstring for the fuller reasoning.

NOT yet verified against a live walmart.com page -- this project's sandbox
can't reach walmart.com directly. Treat the first live run as a real test;
expect to need at least one round of fixes based on what Walmart's actual
page structure turns out to be, the same way both amazon_mx and target
needed adjustments after their first live runs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from restock_sentinel.models import StockCheckResult, TrackedProduct
from restock_sentinel.plugins.base import DEFAULT_USER_AGENT, RetailerPlugin
from restock_sentinel.registry import register_plugin

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path("data/debug")
_PRICE_RE = re.compile(r"[\d,]+\.?\d*")

# Keys that would appear on a Walmart product record, in the shapes publicly
# documented for their Next.js item-page payload. Matched case-insensitively
# and treated as alternatives, since the exact key name isn't confirmed.
_NAME_KEYS = ("name", "title", "productName")
_AVAILABILITY_KEYS = ("availabilityStatus", "availabilityStatusV2")
_PRICE_CONTAINER_KEYS = ("priceInfo",)

# availabilityStatus values that mean "purchasable right now." Everything
# else (OUT_OF_STOCK, PREORDER_BACKORDER, DISCONTINUED, ...) is treated as
# not currently in stock.
_IN_STOCK_VALUES = {"IN_STOCK"}


@register_plugin
class WalmartPlugin(RetailerPlugin):
    name = "walmart"

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
            "walmart GET(rendered) %s -> status=%s bytes=%d",
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

        Mirrors amazon_mx and target: a real browser avoids triggering
        Walmart's bot-detection in the first place, rather than defeating it.
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
            (_DEBUG_DIR / "walmart_last_failure.html").write_text(html, encoding="utf-8")
            (_DEBUG_DIR / "walmart_last_failure_meta.txt").write_text(
                f"status_code: {status_code}\n"
                f"final_url: {final_url}\n"
                f"content_length: {len(html)}\n",
                encoding="utf-8",
            )
            logger.info("Saved failing response to %s for inspection", _DEBUG_DIR)
        except OSError:
            logger.debug("Could not write debug snapshot", exc_info=True)

    def _parse(self, product: TrackedProduct, html: str) -> StockCheckResult:
        next_data = self._extract_next_data(html)
        if next_data is None:
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=None,
                title=None,
                error=(
                    "could not find or parse the __NEXT_DATA__ script -- "
                    "likely a bot-check interstitial, or Walmart has changed "
                    "how this page embeds its data"
                ),
            )

        product_node = self._find_product_node(next_data)
        if product_node is None:
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=None,
                title=None,
                error=(
                    "found __NEXT_DATA__ but no product-shaped record inside "
                    "it -- Walmart may have restructured this page's data"
                ),
            )

        title = self._first_present(product_node, _NAME_KEYS)

        price = None
        for key in _PRICE_CONTAINER_KEYS:
            container = product_node.get(key)
            if isinstance(container, dict):
                price = self._extract_price_from_dict(container)
                if price is not None:
                    break
        if price is None:
            # Fall back to scanning the product node's own fields directly,
            # in case price isn't nested under a sub-object on this listing.
            price = self._extract_price_from_dict(product_node)

        availability_raw = self._first_present(product_node, _AVAILABILITY_KEYS)
        in_stock = availability_raw in _IN_STOCK_VALUES if availability_raw else False

        return StockCheckResult(
            product=product,
            in_stock=in_stock,
            price=price,
            title=title,
            raw_status=availability_raw,
        )

    @staticmethod
    def _extract_next_data(html: str) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")
        script = soup.select_one('script#__NEXT_DATA__[type="application/json"]')
        if script is None:
            script = soup.select_one("script#__NEXT_DATA__")
        if script is None:
            return None

        text = script.string or script.get_text()
        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _find_product_node(cls, data: object, _depth: int = 0) -> dict | None:
        """Walk the parsed JSON tree looking for a product-shaped record.

        A "product-shaped record" is any dict that has a name/title field
        and an availability field, whatever depth it's nested at. This
        avoids hardcoding one exact path that Walmart's actual page may not
        match (see the module docstring). Depth-limited to avoid pathological
        recursion on unexpectedly huge payloads.
        """
        if _depth > 40:
            return None

        if isinstance(data, dict):
            has_name = any(key in data for key in _NAME_KEYS)
            has_availability = any(key in data for key in _AVAILABILITY_KEYS)
            if has_name and has_availability:
                return data
            for value in data.values():
                found = cls._find_product_node(value, _depth + 1)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = cls._find_product_node(item, _depth + 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _first_present(node: dict, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = node.get(key)
            if value:
                return value
        return None

    @staticmethod
    def _extract_price_from_dict(node: dict) -> float | None:
        """Look for the first plausible numeric price among common field
        names, rather than assuming one exact key.
        """
        for key in ("currentPrice", "price", "linePrice", "itemPrice"):
            value = node.get(key)
            if isinstance(value, dict):
                # Some shapes nest the number under e.g. currentPrice.price
                inner = value.get("price")
                if isinstance(inner, (int, float)):
                    return float(inner)
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                match = _PRICE_RE.search(value)
                if match:
                    try:
                        return float(match.group(0).replace(",", ""))
                    except ValueError:
                        continue
        return None
