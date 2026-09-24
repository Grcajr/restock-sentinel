"""Amazon Mexico (amazon.com.mx) stock-check plugin.

Originally written as a plain ``requests`` GET + HTML parse. In practice
(confirmed against a live amazon.com.mx product page, not just assumed),
Amazon serves a soft bot-check interstitial to plain HTTP clients almost
immediately — no image CAPTCHA, just a "click to continue shopping" page
that a simple `requests.get` can't get past, because it lacks a real
browser's TLS/JS fingerprint. So this plugin fetches the page with a real
headless Chromium instance via Playwright instead. That's the same thing a
human's browser does — it isn't solving or bypassing that checkpoint, it
just doesn't trigger it in the first place.

Explicitly out of scope, on purpose: if a page ever *does* render that
click-through interstitial even through a real browser, this plugin does
not click it, submit it, or otherwise automate past it. That endpoint is
Amazon's own automated-access checkpoint (`/errors_page/validateCaptcha`),
and defeating it deliberately is the kind of bypass this project's
responsible-use policy rules out. A result with `error` set and a saved
debug snapshot is the correct outcome in that case, not a workaround.

Notes on fragility, worth calling out explicitly: Amazon changes markup and
serves different layouts based on A/B tests and locale. The selectors below
are deliberately layered (several fallbacks per field) and every parse
failure degrades to ``error`` rather than a crash or a false "in stock".
Treat this as a best-effort integration, not a guarantee — that's true of
scraping-based stock checks in general.
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

_PRICE_RE = re.compile(r"[\d,]+\.?\d*")
_DEBUG_DIR = Path("data/debug")

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
            "amazon_mx GET(rendered) %s -> status=%s bytes=%d",
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

        Using a real browser — rather than solving/automating past any
        anti-bot checkpoint — is what avoids triggering one in the first
        place. See the module docstring for the boundary this plugin does
        not cross.
        """
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT, locale="es-MX")
                response = page.goto(
                    url,
                    timeout=self.PAGE_LOAD_TIMEOUT_SECONDS * 1000,
                    wait_until="domcontentloaded",
                )
                # Some listings (notably multi-seller / marketplace items) populate
                # the availability text via an async JS call after initial render,
                # not in the server-sent HTML. Wait for network activity to settle
                # so that call has a chance to finish before we read the DOM.
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except PlaywrightError:
                    pass  # fine if it never goes fully idle; we still read what we have
                # Then give the specific widget a beat to actually paint the text.
                try:
                    page.wait_for_function(
                        "document.querySelector('.primary-availability-message')"
                        "?.textContent.trim().length > 0",
                        timeout=3_000,
                    )
                except PlaywrightError:
                    pass  # may legitimately never populate for some listing types
                html = page.content()
                status_code = response.status if response else None
                return html, status_code, page.url
            finally:
                browser.close()

    @staticmethod
    def _save_debug_snapshot(html: str, status_code: int | None, final_url: str) -> None:
        """Best-effort dump of the last failing response, for triage.

        Never raises — a failure here should never mask the real error from
        the stock check itself.
        """
        try:
            _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (_DEBUG_DIR / "amazon_mx_last_failure.html").write_text(html, encoding="utf-8")
            (_DEBUG_DIR / "amazon_mx_last_failure_meta.txt").write_text(
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
        availability_text = self._extract_availability_text(soup)
        price = self._extract_price(soup)

        if availability_text is None:
            # Multi-seller (AOD) listings carry a hidden field that's Amazon's own
            # signal for "zero offers currently for sale" — when it's false, an
            # empty availability message means confirmed-out-of-stock, not a
            # parse failure. Found by inspecting a real out-of-stock listing.
            no_offers_field = soup.select_one("#aod-has-oas-offers")
            if no_offers_field is not None and no_offers_field.get("value") == "false":
                return StockCheckResult(
                    product=product,
                    in_stock=False,
                    price=price,
                    title=title,
                    raw_status="no offers currently available (multi-seller listing)",
                )

            # Otherwise: could be a bot-check interstitial, or a listing whose
            # availability widget never populated for some other reason —
            # degrade honestly rather than guess.
            return StockCheckResult(
                product=product,
                in_stock=False,
                price=price,
                title=title,
                raw_status=None,
                error=(
                    "could not locate availability text (bot-check page, "
                    "an unrendered async widget, or a listing with no single "
                    "primary seller)"
                ),
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
        # `.primary-availability-message` is the element multi-seller/marketplace
        # listings use (populated async, see _fetch_rendered_html); the rest cover
        # the simpler single-Buy-Box layout.
        for selector in (
            ".primary-availability-message",
            "#availability .a-declarative",
            "#availability span",
            "#availability",
        ):
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
