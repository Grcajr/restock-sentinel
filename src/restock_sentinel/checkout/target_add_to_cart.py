"""Target automated "win the race to add it to cart" helper.

Scope, on purpose: this gets the item into your Target cart the moment it's
in stock, using your own saved login (see ``sessions.py``) so your account's
default address and payment are already attached -- and stops exactly
there. It never proceeds to checkout or clicks "Place your order." That's a
deliberate line, same one this project draws everywhere else: a human
confirms the actual purchase. What this automates is the part that's pure
speed -- getting into the cart before it sells out -- not the part that's a
real financial decision.

Why the retry loop exists, and why it's not evasion: on a real high-demand
drop (the motivating case here is a Pokemon collectible restock), Target's
own site visibly struggles under the load of everyone -- bots and real
people both -- hitting the same page at once. Real, manual experience with
this: the cart page itself sometimes won't load and needs ~20 refreshes,
and even clicking "Place your order" can take another ~20 tries before it
goes through. That's Target's infrastructure being overwhelmed by demand,
not a bot-check -- there's no CAPTCHA or "are you human" step anywhere in
this flow, just an ordinary page that times out or errors under load. So
this retries the same way a person does: reload and try again, with a
short randomized pause between attempts (not a tight hammering loop), up to
``max_attempts`` times. If every attempt fails, that's reported honestly as
a failure, not disguised as a success.

NOT yet live-tested -- this project's sandbox can't reach target.com. The
container/button selectors for the plain add-to-cart case reuse what the
target.py stock-check plugin already confirmed against a real sold-out
listing (see that module's docstring). The "Choose options" fulfillment
modal that some listings show before actually adding to cart, and the
cart-confirmation check, are new best-guess territory based on the
product's own screenshots, not yet confirmed selectors -- treat the first
live run as a real test, the same as every other plugin in this project.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from restock_sentinel.sessions import SESSIONS_DIR, has_saved_session

logger = logging.getLogger(__name__)

_CLICK_TIMEOUT_MS = 8_000
_NAV_TIMEOUT_MS = 15_000
_MODAL_APPEAR_TIMEOUT_MS = 2_000
_CONFIRM_TIMEOUT_MS = 5_000

DEFAULT_MAX_ATTEMPTS = 25
DEFAULT_RETRY_DELAY_SECONDS = 2.0


@dataclass(slots=True)
class AddToCartResult:
    success: bool
    attempts: int
    screenshot_path: str | None = None
    error: str | None = None


def add_to_cart_target(
    product_url: str,
    *,
    account_label: str = "mine",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    headless: bool = False,
    screenshot_dir: str | Path = "data/screenshots",
) -> AddToCartResult:
    """Race to add ``product_url`` to the Target cart using a saved session.

    Requires a session already captured via ``setup-session`` for
    ``account_label`` (so the account is logged in and has a default address
    and payment on file -- see the module docstring for why this never
    enters payment info itself). Runs headed by default (``headless=False``)
    so you can see it working and jump in the moment it succeeds; pass
    ``headless=True`` if you'd rather it run invisibly.
    """
    if not has_saved_session(account_label):
        return AddToCartResult(
            success=False,
            attempts=0,
            error=(
                f"no saved session for account '{account_label}' -- run "
                f"`restock-sentinel setup-session --account {account_label} "
                "--login-url https://www.target.com/login` first"
            ),
        )

    screenshot_dir = Path(screenshot_dir)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = SESSIONS_DIR / account_label

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile_dir), headless=headless)
        try:
            page = context.new_page()
            page.goto(product_url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")

            for attempt in range(1, max_attempts + 1):
                try:
                    if _try_add_to_cart_once(page):
                        screenshot_path = screenshot_dir / "target_add_to_cart_success.png"
                        page.screenshot(path=str(screenshot_path))
                        logger.info("Added to cart on attempt %d/%d", attempt, max_attempts)
                        return AddToCartResult(
                            success=True,
                            attempts=attempt,
                            screenshot_path=str(screenshot_path),
                        )
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    logger.debug("Add-to-cart attempt %d/%d failed: %s", attempt, max_attempts, exc)

                if attempt == max_attempts:
                    break

                # See the module docstring: this mirrors the manual
                # refresh-and-retry cycle a person already does by hand
                # during a contested drop, not an attempt to evade anything.
                time.sleep(retry_delay_seconds + random.uniform(0, retry_delay_seconds))
                try:
                    page.reload(timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                except (PlaywrightTimeoutError, PlaywrightError):
                    pass  # the next loop iteration will just try again

            # Dump the final page so a failed run is diagnosable. The
            # add-to-cart / fulfillment-modal / confirmation selectors here
            # are best-guesses until confirmed against a real in-stock
            # listing (see the module docstring), and this HTML is how we
            # find out which one missed -- same debug-snapshot pattern the
            # target.py stock-check plugin already uses.
            debug_dir = Path("data/debug")
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / "target_add_to_cart_last_failure.html").write_text(
                    page.content(), encoding="utf-8"
                )
            except (OSError, PlaywrightError):
                pass

            screenshot_path = screenshot_dir / "target_add_to_cart_failure.png"
            try:
                page.screenshot(path=str(screenshot_path))
            except PlaywrightError:
                screenshot_path = None
            return AddToCartResult(
                success=False,
                attempts=max_attempts,
                screenshot_path=str(screenshot_path) if screenshot_path else None,
                error=f"could not confirm add-to-cart after {max_attempts} attempts",
            )
        finally:
            context.close()


def _try_add_to_cart_once(page: Page) -> bool:
    """One attempt: click Add to Cart, handle the fulfillment-choice modal if
    Target shows one, and only report success once the cart is actually
    confirmed to contain the item -- a click that didn't error is not
    treated as proof it worked.
    """
    # Confirmed against a real target.com listing by the stock-check plugin
    # (see plugins/target.py) -- reused here rather than guessed fresh.
    cart_module = page.locator('[data-test="module-product-detail-add-to-cart"]')
    add_to_cart_button = cart_module.locator('button[data-test^="AddToCart."]').first
    add_to_cart_button.click(timeout=_CLICK_TIMEOUT_MS)

    _handle_fulfillment_modal_if_present(page)

    return _confirm_added_to_cart(page)


def _handle_fulfillment_modal_if_present(page: Page) -> None:
    """Some listings open a "Choose options" panel to pick a fulfillment
    method (pickup/delivery/shipping) before the item is actually added,
    rather than adding it directly on the first click. Not yet confirmed
    which listings do this or the panel's exact markup -- this looks for a
    dialog-like container and, if one shows up, clicks whichever option
    Target has already pre-selected (the fastest safe default) and confirms
    with that panel's own "Add to cart" button. If no such panel appears
    within a couple seconds, this assumes the first click already added the
    item directly and does nothing further.
    """
    modal = page.locator('[role="dialog"]').first
    try:
        if not modal.is_visible(timeout=_MODAL_APPEAR_TIMEOUT_MS):
            return
    except PlaywrightTimeoutError:
        return

    modal_confirm_button = modal.get_by_role("button", name="Add to cart", exact=False).first
    modal_confirm_button.click(timeout=_CLICK_TIMEOUT_MS)


def _confirm_added_to_cart(page: Page) -> bool:
    """Best-effort confirmation that the item actually landed in the cart.

    Not yet confirmed against a real page -- looks for either an on-page
    "added to cart" style confirmation, or (if that doesn't show up) checks
    the cart page itself for a non-empty cart. Conservative on purpose: if
    neither signal is found, this reports failure rather than guessing
    success, so a flaky click never gets reported as "you're in the cart"
    when you're not.
    """
    added_toast = page.get_by_text("added to cart", exact=False).first
    try:
        if added_toast.is_visible(timeout=_CONFIRM_TIMEOUT_MS):
            return True
    except PlaywrightTimeoutError:
        pass

    try:
        page.goto(
            "https://www.target.com/cart", timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded"
        )
    except (PlaywrightTimeoutError, PlaywrightError):
        return False

    empty_cart_message = page.get_by_text("cart is empty", exact=False).first
    try:
        if empty_cart_message.is_visible(timeout=_CONFIRM_TIMEOUT_MS):
            return False
    except PlaywrightTimeoutError:
        pass  # no "empty" message found -- keep checking for a positive signal

    checkout_button = page.get_by_role("button", name="Check out", exact=False).first
    try:
        return checkout_button.is_visible(timeout=_CONFIRM_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return False
