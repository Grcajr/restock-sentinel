"""Command-line entry point: `restock-sentinel watch --retailer amazon_mx --url ...`."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from restock_sentinel.alerts.discord import DiscordAlerter
from restock_sentinel.config import Config
from restock_sentinel.db import Database
from restock_sentinel.models import TrackedProduct
from restock_sentinel.registry import get_plugin, list_plugins, load_builtin_plugins

logger = logging.getLogger("restock_sentinel")


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

        was_in_stock = db.was_previously_in_stock(product_id)
        if result.is_purchasable and not was_in_stock and alerter:
            sent = alerter.send_restock_alert(result)
            db.log_alert(product_id, "discord", sent)
            if sent:
                logger.info("Restock alert sent for %s", product.display_name)

        if args.once:
            return 0

        time.sleep(config.check_interval_seconds)


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
