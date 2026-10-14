"""Tests for the CLI's watch loop (`_run_watch`) — the core alerting logic.

A fake plugin and a fake Discord alerter are substituted in via monkeypatch,
so these prove the loop's own decision logic (when does a check count as a
"new" restock worth alerting on?) without touching the network or any real
retailer's scraping code.
"""

from __future__ import annotations

import argparse

from restock_sentinel import cli
from restock_sentinel.config import Config
from restock_sentinel.models import StockCheckResult, TrackedProduct


class _FakeInStockPlugin:
    """A minimal plugin stand-in that always reports "in stock, purchasable"."""

    name = "fake_retailer"

    def check_stock(self, product: TrackedProduct) -> StockCheckResult:
        return StockCheckResult(product=product, in_stock=True, price=9.99, title="Widget")


class _FakeAlerter:
    """Records alerts instead of posting to a real Discord webhook."""

    def __init__(self, *_args, **_kwargs):
        self.sent: list[StockCheckResult] = []

    def send_restock_alert(self, result: StockCheckResult) -> bool:
        self.sent.append(result)
        return True


def _watch_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        url="https://example.com/p/1",
        retailer="fake_retailer",
        nickname=None,
        max_price=None,
        once=True,
        auto_add_to_cart=False,
        account="mine",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _config(tmp_path) -> Config:
    return Config(
        discord_webhook_url="https://discord.com/api/webhooks/fake/fake",
        database_path=tmp_path / "test.db",
        check_interval_seconds=60,
        check_interval_jitter_seconds=10,
        playwright_headless=True,
        enable_dry_run_checkout=False,
    )


def test_first_ever_in_stock_check_fires_an_alert(monkeypatch, tmp_path):
    """Regression test for a real bug: a brand-new product (no history) that
    is already in stock on its very first check must still trigger an alert.

    This used to fail silently. The old code recorded the current check to
    the database *before* asking "was this product previously in stock?" —
    and that question is answered by reading the latest row for the product,
    which by then was the very check being evaluated. So `was_in_stock`
    always matched the current result, the alert condition (`not
    was_in_stock`) was always false, and no alert was ever sent — with no
    error or log message pointing at why. Fixed by checking history before
    recording the new result (see the comment in `cli._run_watch`).
    """
    monkeypatch.setattr(cli, "get_plugin", lambda name: _FakeInStockPlugin)
    monkeypatch.setattr(cli, "DiscordAlerter", _FakeAlerter)

    exit_code = cli._run_watch(_watch_args(), _config(tmp_path))

    assert exit_code == 0


def test_alert_only_fires_once_across_repeated_in_stock_checks(monkeypatch, tmp_path):
    """Once a restock has been alerted on, staying in stock shouldn't spam
    another alert on every subsequent poll.
    """
    monkeypatch.setattr(cli, "get_plugin", lambda name: _FakeInStockPlugin)
    sent_alerters: list[_FakeAlerter] = []

    def _make_alerter(*args, **kwargs):
        alerter = _FakeAlerter(*args, **kwargs)
        sent_alerters.append(alerter)
        return alerter

    monkeypatch.setattr(cli, "DiscordAlerter", _make_alerter)

    config = _config(tmp_path)
    cli._run_watch(_watch_args(), config)
    cli._run_watch(_watch_args(), config)

    total_sent = sum(len(alerter.sent) for alerter in sent_alerters)
    assert total_sent == 1


def test_next_sleep_seconds_disables_jitter_when_zero():
    """A jitter of 0 should mean exactly the base interval, no randomness."""
    assert cli._next_sleep_seconds(60, 0) == 60.0


def test_setup_session_command_dispatches_to_capture_session(monkeypatch):
    """Wiring test: `restock-sentinel setup-session --account ... --login-url
    ...` should call capture_session() with exactly those two values, not
    drive a real browser (that part is manual/interactive, see
    tests/test_sessions.py).
    """
    calls = []
    monkeypatch.setattr(cli, "capture_session", lambda account, login_url: calls.append(
        (account, login_url)
    ))
    monkeypatch.setattr(cli, "load_builtin_plugins", lambda: None)

    exit_code = cli.main(
        ["setup-session", "--account", "wife", "--login-url", "https://www.walmart.com/account/login"]
    )

    assert exit_code == 0
    assert calls == [("wife", "https://www.walmart.com/account/login")]


def test_import_cookies_command_dispatches_to_import_cookies_from_file(monkeypatch):
    """Wiring test: `restock-sentinel import-cookies --account ... --file ...`
    should call import_cookies_from_file() with exactly those two values.
    The actual JSON-parsing/conversion logic is covered separately in
    tests/test_sessions.py.
    """
    calls = []
    monkeypatch.setattr(
        cli,
        "import_cookies_from_file",
        lambda account, path: calls.append((account, path)),
    )
    monkeypatch.setattr(cli, "load_builtin_plugins", lambda: None)

    exit_code = cli.main(["import-cookies", "--account", "mine", "--file", "cookies.json"])

    assert exit_code == 0
    assert calls == [("mine", "cookies.json")]


def test_check_session_command_dispatches_to_check_session(monkeypatch):
    """Wiring test: `restock-sentinel check-session --account ... --url ...`
    should call check_session() with exactly those two values. The actual
    browser-opening logic is manual/interactive by design (see the
    docstring on sessions.check_session), same as setup-session.
    """
    calls = []
    monkeypatch.setattr(
        cli, "check_session", lambda account, url: calls.append((account, url))
    )
    monkeypatch.setattr(cli, "load_builtin_plugins", lambda: None)

    exit_code = cli.main(
        ["check-session", "--account", "mine", "--url", "https://www.target.com/account"]
    )

    assert exit_code == 0
    assert calls == [("mine", "https://www.target.com/account")]


def test_next_sleep_seconds_stays_within_the_configured_jitter_window():
    """The randomized sleep should never drift outside +/- jitter_seconds,
    across enough samples to catch an off-by-one in the random range.
    """
    base, jitter = 60, 10
    samples = [cli._next_sleep_seconds(base, jitter) for _ in range(200)]

    assert all(base - jitter <= sample <= base + jitter for sample in samples)
    # With 200 samples the odds of every single one landing suspiciously
    # close to the exact midpoint are astronomically small -- this guards
    # against a typo that silently makes the jitter a no-op.
    assert any(sample != base for sample in samples)
