"""Manual browser-session capture for actions that need a logged-in account.

Some actions -- like entering a timed drawing/raffle -- need an
authenticated account, not just a public page. Automating a fresh login for
that is more fragile and more sensitive than the read-only plugins: it
means handling a real password, and login is usually exactly where 2FA
lives. Rather than automate the login step itself, this captures a real
session by opening a normal, VISIBLE browser window, letting a person log
in themselves -- including any 2FA/verification prompt -- and saving that
session to disk. Later automation reuses the saved session instead of
logging in fresh, the same way a regular browser profile stays signed in
across restarts without re-entering a password every time.

This deliberately does not touch 2FA at all: the human completes it once,
normally, in a real browser window they can see. Nothing here reads codes,
stores a password, or automates a login form.

Session data is a real, sensitive credential -- equivalent to a saved
password -- and lives under ``data/sessions/<account_label>/``, which
inherits the project's existing ``data/`` gitignore rule. Never commit this
directory. Sessions do eventually expire (the retailer invalidates them, or
enough time passes); when that happens the next automated use will simply
fail to find a logged-in page, and the fix is to run this capture step
again.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path("data/sessions")

DEFAULT_CAPTURE_TIMEOUT_MINUTES = 15


def capture_session(
    account_label: str,
    login_url: str,
    *,
    timeout_minutes: int = DEFAULT_CAPTURE_TIMEOUT_MINUTES,
) -> Path:
    """Open a real, visible browser window for a person to log into by hand.

    Blocks until that browser window is closed. Whatever cookies/local
    storage state exists in the profile at that point is what later
    automation will reuse -- so the expectation is: log in fully (including
    any 2FA/verification step), confirm you land on a normal logged-in
    page, and then close the browser window yourself to finish.

    Returns the profile directory the session was saved to.
    """
    profile_dir = SESSIONS_DIR / account_label
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
        )
        try:
            page = context.new_page()
            page.goto(login_url)
            print(
                f"\nA browser window has opened for account '{account_label}'.\n"
                "Log in fully -- including any 2FA/verification step -- then "
                "just close that browser window once you see you're logged in.\n"
                f"(This will wait up to {timeout_minutes} minutes.)\n"
            )
            context.wait_for_event("close", timeout=timeout_minutes * 60 * 1000)
        finally:
            # If the window is already closed this just no-ops; if the
            # timeout was hit instead, this cleans up rather than leaving a
            # dangling browser process.
            try:
                context.close()
            except Exception:
                pass

    logger.info("Session saved for '%s' at %s", account_label, profile_dir)
    return profile_dir


_SAME_SITE_MAP = {
    "strict": "Strict",
    "lax": "Lax",
    "no_restriction": "None",
    # Chrome's own modern default treats an unset/"unspecified" SameSite as
    # Lax. Playwright has no matching "unspecified" value, so this picks
    # the closest safe default instead of erroring on it.
    "unspecified": "Lax",
}


def _convert_cookie_editor_cookie(raw: dict) -> dict:
    """Convert one cookie from a Cookie-Editor JSON export to the shape
    Playwright's ``context.add_cookies()`` expects.

    Cookie-Editor exports cookies in the same shape as Chrome's own
    extension cookie API (``chrome.cookies.getAll()``), which differs from
    Playwright's format in two ways: expiry is ``expirationDate`` (a float
    Unix timestamp) instead of ``expires``, and ``sameSite`` uses lowercase
    values (``"lax"``, ``"strict"``, ``"no_restriction"``, ``"unspecified"``)
    instead of Playwright's capitalized ones (``"Lax"``, ``"Strict"``,
    ``"None"``). This only reshapes those fields -- it never inspects or
    logs the actual cookie name/value being converted.
    """
    cookie = {
        "name": raw["name"],
        "value": raw["value"],
        "domain": raw["domain"],
        "path": raw.get("path", "/"),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure": bool(raw.get("secure", False)),
        "sameSite": _SAME_SITE_MAP.get(str(raw.get("sameSite", "")).lower(), "Lax"),
    }

    if raw.get("session"):
        cookie["expires"] = -1
    else:
        expiration = raw.get("expirationDate")
        cookie["expires"] = float(expiration) if expiration is not None else -1

    return cookie


def import_cookies_from_file(account_label: str, cookies_path: str | Path) -> Path:
    """Build a saved session for ``account_label`` from a Cookie-Editor export.

    This is the alternative to ``capture_session()`` for sites that block a
    Playwright-driven browser at the login step itself -- even with a real
    human doing the clicking -- because Chromium sets a standard automation
    flag (``navigator.webdriver``) on any browser launched that way, and
    some login flows check for it. Rather than fight that, this never puts
    an automated browser anywhere near the login page at all: the person
    logs in completely normally, in their own everyday Chrome, and this
    just reuses the session that already exists.

    ``cookies_path`` should point at a JSON file produced by the
    "Cookie-Editor" browser extension's "Export as JSON" option, saved
    after logging into the target site normally in that browser. This
    function only reads that local file and writes into
    ``data/sessions/<account_label>/`` -- it's meant to be run directly on
    whatever machine the export was saved to, so the actual cookie values
    never need to leave that machine.

    Returns the profile directory the session was saved to.
    """
    cookies_path = Path(cookies_path)
    raw_cookies = json.loads(cookies_path.read_text(encoding="utf-8"))

    if not isinstance(raw_cookies, list) or not raw_cookies:
        raise ValueError(
            f"Expected a non-empty JSON list of cookies in {cookies_path} -- "
            "that's what Cookie-Editor's 'Export as JSON' produces. Got "
            f"{type(raw_cookies).__name__} instead."
        )

    cookies = [_convert_cookie_editor_cookie(raw) for raw in raw_cookies]

    profile_dir = SESSIONS_DIR / account_label
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile_dir), headless=True)
        try:
            context.add_cookies(cookies)
        finally:
            context.close()

    logger.info(
        "Imported %d cookie(s) for '%s' into %s", len(cookies), account_label, profile_dir
    )
    return profile_dir


DEFAULT_CHECK_TIMEOUT_MINUTES = 5


def check_session(
    account_label: str,
    check_url: str,
    *,
    timeout_minutes: int = DEFAULT_CHECK_TIMEOUT_MINUTES,
) -> Path:
    """Open a saved/imported session in a real, visible browser against a
    page that only shows real content when you're actually logged in, so a
    human can look at the screen and confirm the session is genuinely valid
    -- before anything automated (like the add-to-cart race) relies on it.

    This exists because `import_cookies_from_file()` succeeding only proves
    the JSON parsed and the cookies were written to the browser profile --
    it says nothing about whether those cookies still authenticate on the
    retailer's side (expired, captured before 2FA finished, wrong account,
    etc.). Rather than have code guess at "logged in" from page text (easy
    to get wrong and report false confidence), this just opens the page and
    waits for a person to look and close the window themselves -- same
    manual-confirmation pattern as `capture_session()`.

    Raises if no saved session exists for ``account_label`` at all.
    """
    if not has_saved_session(account_label):
        raise ValueError(
            f"no saved session for account '{account_label}' -- run "
            f"`setup-session` or `import-cookies` for it first"
        )

    profile_dir = SESSIONS_DIR / account_label

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile_dir), headless=False)
        try:
            page = context.new_page()
            page.goto(check_url)
            print(
                f"\nOpened {check_url} using the saved session for '{account_label}'.\n"
                "Look at the window: if it shows your real account (not a sign-in "
                "page), the session is good to use. Close the window when you're "
                f"done looking. (This will wait up to {timeout_minutes} minutes.)\n"
            )
            context.wait_for_event("close", timeout=timeout_minutes * 60 * 1000)
        finally:
            try:
                context.close()
            except Exception:
                pass

    return profile_dir


def has_saved_session(account_label: str) -> bool:
    """Whether a capture_session() has been run for this account before.

    A cheap existence check only -- it does not verify the saved session is
    still valid (still logged in) on the retailer's side.
    """
    profile_dir = SESSIONS_DIR / account_label
    return profile_dir.exists() and any(profile_dir.iterdir())
