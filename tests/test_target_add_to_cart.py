"""Tests for the Target auto-add-to-cart helper's pure pre-flight logic.

The actual browser-driving retry loop (clicking Add to Cart, handling the
fulfillment-choice modal, confirming the cart) needs a real browser against
a real page to mean anything -- consistent with how this project tests
every other Playwright-driven piece (see plugins/*.py and
checkout/playwright_checkout.py, none of which unit-test their browser
automation directly). What IS tested here, with no browser involved at
all, is the one guard that matters most to get right: never attempt to
drive a browser for an account that was never actually logged in.
"""

from __future__ import annotations

from restock_sentinel import sessions
from restock_sentinel.checkout.target_add_to_cart import add_to_cart_target


def test_refuses_to_run_without_a_saved_session(tmp_path, monkeypatch):
    """No saved session for the requested account must fail immediately and
    honestly, without ever launching a browser (which would just hit a
    logged-out page and silently fail later instead).
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")

    result = add_to_cart_target(
        "https://www.target.com/p/example/-/A-12345678", account_label="mine"
    )

    assert result.success is False
    assert result.attempts == 0
    assert "no saved session" in result.error
    assert "mine" in result.error


def test_different_accounts_are_checked_independently(tmp_path, monkeypatch):
    """A saved session for one account shouldn't let a different,
    never-captured account slip past the guard.
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    mine_dir = tmp_path / "sessions" / "mine"
    mine_dir.mkdir(parents=True)
    (mine_dir / "Default").mkdir()

    result = add_to_cart_target(
        "https://www.target.com/p/example/-/A-12345678", account_label="wife"
    )

    assert result.success is False
    assert result.attempts == 0
    assert "wife" in result.error
