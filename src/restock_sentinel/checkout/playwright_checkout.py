"""Playwright-driven dry-run checkout.

This walks a real browser through add-to-cart -> checkout, exactly like a
human buying the item, and stops at the final payment-confirmation step
instead of submitting an order. It exists to prove (and screenshot/log) that
the automation can reliably reach checkout the moment a restock is detected
— without ever placing a real, unattended purchase.

Deliberately NOT hooked up to real payment submission anywhere in this
codebase. If you extend this to complete real purchases, that's a decision
to make consciously and separately, with its own safeguards (spend caps,
confirmation step, etc.) — not something this module does by default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS = 15_000


@dataclass(slots=True)
class DryRunResult:
    reached_checkout: bool
    screenshot_path: str | None
    error: str | None = None


def dry_run_checkout_amazon_mx(
    product_url: str,
    *,
    headless: bool = True,
    screenshot_dir: str | Path = "data/screenshots",
) -> DryRunResult:
    """Add ``product_url`` to the Amazon.mx cart and proceed to checkout.

    Stops as soon as the checkout / order-review page loads — no payment
    method is ever selected or submitted. Takes a screenshot at that point
    as proof-of-reach for logging/debugging.
    """
    screenshot_dir = Path(screenshot_dir)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(product_url, timeout=_DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")

            buy_now = page.locator("#buy-now-button")
            add_to_cart = page.locator("#add-to-cart-button")

            if buy_now.count() > 0:
                buy_now.click(timeout=_DEFAULT_TIMEOUT_MS)
            elif add_to_cart.count() > 0:
                add_to_cart.click(timeout=_DEFAULT_TIMEOUT_MS)
                page.goto(
                    "https://www.amazon.com.mx/gp/cart/view.html",
                    timeout=_DEFAULT_TIMEOUT_MS,
                )
                page.locator("input[name='proceedToRetailCheckout']").first.click(
                    timeout=_DEFAULT_TIMEOUT_MS
                )
            else:
                return DryRunResult(
                    reached_checkout=False,
                    screenshot_path=None,
                    error="neither Buy Now nor Add to Cart button found",
                )

            # Landing on any /checkout/ or order-review page counts as
            # "reached checkout" — we go no further than that.
            page.wait_for_url("**/checkout/**", timeout=_DEFAULT_TIMEOUT_MS)

            screenshot_path = screenshot_dir / "amazon_mx_checkout_dry_run.png"
            page.screenshot(path=str(screenshot_path))
            return DryRunResult(reached_checkout=True, screenshot_path=str(screenshot_path))

        except PlaywrightTimeoutError as exc:
            return DryRunResult(
                reached_checkout=False, screenshot_path=None, error=f"timeout: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - surface any failure without crashing the loop
            logger.exception("Dry-run checkout failed for %s", product_url)
            return DryRunResult(reached_checkout=False, screenshot_path=None, error=str(exc))
        finally:
            browser.close()
