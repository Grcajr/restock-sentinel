"""Target (target.com) stock-check plugin.

First attempt at this plugin assumed Target published schema.org JSON-LD
structured product data (the way many e-commerce sites do), the same way
the amazon_mx plugin was later followed by a Target implementation. A live
test against a real target.com listing (see the module history / commit
log) showed that assumption was wrong -- Target's product pages carry no
JSON-LD at all. What actually works, confirmed against that real page:

* The product title lives in an ``<h1 data-test="product-title">``.
* The price lives in a ``[data-test="price-cdui"]`` block.
* Purchasability is the real signal worth trusting: inside
  ``[data-test="module-product-detail-add-to-cart"]``, the Add to Cart
  button carries a genuine HTML ``disabled`` attribute (and
  ``data-component-state="disabled"``) when the item can't be bought right
  now. That's Target's own UI logic telling us the answer directly, rather
  than us inferring it from availability text.

Like the Amazon.mx plugin, pages are fetched with a real headless browser
(Playwright) rather than a plain HTTP client -- large retailers commonly
run bot-detection that blocks simple scripted requests outright, and using
a real browser is what avoids triggering that in the first place, not a
way of defeating it. If a page ever renders an explicit bot-check/CAPTCHA
interstitial instead of the product, this plugin does not attempt to click
through or solve it: a result with ``error`` set and a saved debug
snapshot is the correct, honest outcome. See the amazon_mx plugin's module
docstring for the fuller reasoning behind that boundary; it applies here
unchanged.

Known gap: the in-stock fixture in tests/test_target.py is a best-guess
based on the disabled-button signal's absence, not yet confirmed against a
real in-stock listing (the only live test done so far happened to hit a
sold-out item). Also untested: listings that show a "choose options"
variant picker instead of a direct Add to Cart button -- those are
conservatively treated as not-purchasable for now (see ``_parse``), the
same false-negatives-over-false-positives tradeoff amazon_mx makes for its
multi-seller listings.
"""

from __future__ import annotations

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


@register_plugin
class TargetPlugin(RetailerPlugin):
    name = "target"

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
            "target GET(rendered) %s -> status=%s bytes=%d",
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

        Mirrors amazon_mx's approach: a real browser is what avoids
        triggering Target's bot-detection in the first place, rather than
        something that defeats it. See the module docstring.
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
            (_DEBUG_DIR / "target_last_failure.html").write_text(html, encoding="utf-8")
            (_DEBUG_DIR / "target_last_failure_meta.txt").write_text(
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
        title = self._extract_title(soup)
        price = self._extract_price(soup)

        cart_module = soup.select_one('[data-test="module-product-detail-add-to-cart"]')
        if cart_module is None:
            # Could be a bot-check interstitial, or Target has changed this
            # page's layout entirely -- degrade honestly rather than guess.
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=price,
                title=title,
                error=(
                    "could not find the add-to-cart module on the page -- "
                    "likely a bot-check interstitial, or Target has changed "
                    "this page's layout"
                ),
            )

        add_to_cart_button = cart_module.select_one('button[data-test^="AddToCart."]')
        if add_to_cart_button is None:
            # Most likely a "choose options" variant-picker listing instead
            # of a direct Add to Cart button. We can't confidently tell
            # purchasability apart from a variant-selection requirement
            # here, so conservatively report not-purchasable rather than
            # guessing -- false negatives are the safer failure mode for a
            # restock alert. See the module docstring's "known gap" note.
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=price,
                title=title,
                raw_status=(
                    "no direct Add to Cart button found (possible variant-selection listing)"
                ),
            )

        is_disabled = add_to_cart_button.has_attr("disabled") or (
            add_to_cart_button.get("data-component-state") == "disabled"
        )

        return StockCheckResult(
            product=product,
            in_stock=not is_disabled,
            price=price,
            title=title,
            raw_status=add_to_cart_button.get("data-test"),
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str | None:
        node = soup.select_one('[data-test="product-title"]') or soup.select_one("h1")
        return node.get_text(strip=True) if node else None

    @staticmethod
    def _extract_price(soup: BeautifulSoup) -> float | None:
        price_selectors = (
            '[data-test="price-cdui"]',
            '[data-test="module-product-detail-price-v2"]',
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
