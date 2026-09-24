"""Command-line entry point: `restock-sentinel watch --retailer amazon_mx --url ...`."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from restock_sentinel.alerts.discord import DiscordAlerter
from restock_sentinel.config import Config
from restock_sentinel.db import Database
from restock_sentinel.models import TrackedProduct
from restock_sentinel.registry import get_plugin, list_plugins, load_builtin_plugins

logger = logging.getLogger("restock_sentinel")


def _next_sleep_seconds(base_seconds: int, jitter_seconds: int) -> float:
    """Randomize the wait between checks by up to +/- ``jitter_seconds``.

    Polling a page at an exact, unvarying interval forever (60.000 seconds,
    every single time) is a distinctly non-human pattern — a person
    refreshing a page doesn't do it on a metronome. Adding a small random
    wobble around the configured interval makes the traffic pattern look
    like what it actually is (a periodic check), just without the
    perfectly mechanical cadence, and it's a bit gentler on the site than
    hammering it at a fixed beat. This is about polite, less-robotic
    pacing — it has nothing to do with hiding what the requests are.
    """
    if jitter_seconds <= 0:
        return float(base_seconds)
    return base_seconds + random.uniform(-jitter_seconds, jitter_seconds)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restock-sentinel")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", help="poll a product until it's in stock, then alert")
    watch.add_argument("--url", required=True, help="product URL to watch")
    watch.add_argument("--retailer", required=True, help="registered plugin name, e.g. amazon_mx")
    watch.add_argument("--nickname", default=None, help="friendly name for logs/alerts")
    watch.add_argument("--max-price", type=float, default=None)
    watch.add_argument("--once", action="store_true", help="check once and exit, no loop")

    sub.add_parser("list-retailers", help="list registered retailer plugins")

    return parser


def _run_watch(args: argparse.Namespace, config: Config) -> int:
    plugin_cls = get_plugin(args.retailer)
    plugin = plugin_cls()

    product = TrackedProduct(
        url=args.url,
        retailer=args.retailer,
        nickname=args.nickname,
        max_price=args.max_price,
    )

    db = Database(config.database_path)
    product_id = db.upsert_product(product)

    alerter = DiscordAlerter(config.discord_webhook_url) if config.discord_webhook_url else None

    logger.info("Watching %s (%s)", product.display_name, args.retailer)

    while True:
        result = plugin.check_stock(product)

        # Ask "was it in stock before?" BEFORE writing this check to history.
        # record_stock_check() below inserts a new row, and was_previously_in_stock()
        # just reads the latest row for this product — if that write happened
        # first, "the latest row" would be the check we're deciding on, so this
        # would always compare the result to itself and the alert below would
        # never fire. See tests/test_cli.py for the regression test.
        was_in_stock = db.was_previously_in_stock(product_id)
        db.record_stock_check(product_id, result)

        if result.error:
            logger.warning("Check failed: %s", result.error)
        else:
            logger.info(
                "%s -> in_stock=%s price=%s",
                product.display_name,
                result.in_stock,
                result.price,
            )

        if result.is_purchasable and not was_in_stock and alerter:
            sent = alerter.send_restock_alert(result)
            db.log_alert(product_id, "discord", sent)
            if sent:
                logger.info("Restock alert sent for %s", product.display_name)

        if args.once:
            return 0

        sleep_seconds = _next_sleep_seconds(
            config.check_interval_seconds, config.check_interval_jitter_seconds
        )
        time.sleep(sleep_seconds)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    load_builtin_plugins()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "list-retailers":
        for name in list_plugins():
            print(name)
        return 0

    config = Config.load()

    if args.command == "watch":
        try:
            return _run_watch(args, config)
        except KeyboardInterrupt:
            logger.info("Stopped.")
            return 0

    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
