"""Command-line entry point: `restock-sentinel watch --retailer amazon_mx --url ...`."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

from restock_sentinel.alerts.discord import DiscordAlerter
from restock_sentinel.checkout.target_add_to_cart import add_to_cart_target
from restock_sentinel.config import Config
from restock_sentinel.db import Database
from restock_sentinel.models import TrackedProduct
from restock_sentinel.registry import get_plugin, list_plugins, load_builtin_plugins
from restock_sentinel.sessions import capture_session, check_session, import_cookies_from_file

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
    watch.add_argument(
        "--auto-add-to-cart",
        action="store_true",
        help=(
            "on restock, automatically add the item to cart (Target only, for now) "
            "using a saved session -- stops there, never checks out. See "
            "checkout/target_add_to_cart.py"
        ),
    )
    watch.add_argument(
        "--account",
        default="mine",
        help="which saved session (from `setup-session`) to use with --auto-add-to-cart",
    )

    sub.add_parser("list-retailers", help="list registered retailer plugins")

    setup_session = sub.add_parser(
        "setup-session",
        help="log into an account once, by hand, so later automation can reuse the session",
    )
    setup_session.add_argument(
        "--account", required=True, help="a short label for this account, e.g. 'mine' or 'wife'"
    )
    setup_session.add_argument(
        "--login-url", required=True, help="the retailer's login page URL"
    )

    import_cookies = sub.add_parser(
        "import-cookies",
        help=(
            "build a session from a Cookie-Editor JSON export, for sites that block "
            "an automated browser at the login step even when a human is driving it"
        ),
    )
    import_cookies.add_argument(
        "--account", required=True, help="a short label for this account, e.g. 'mine' or 'wife'"
    )
    import_cookies.add_argument(
        "--file",
        required=True,
        help="path to the JSON file exported from the Cookie-Editor browser extension",
    )

    check_session_cmd = sub.add_parser(
        "check-session",
        help=(
            "open a saved/imported session in a real visible browser so you can "
            "confirm by eye it's actually logged in, before relying on it"
        ),
    )
    check_session_cmd.add_argument(
        "--account", required=True, help="the account label to check, e.g. 'mine'"
    )
    check_session_cmd.add_argument(
        "--url",
        default="https://www.target.com/account",
        help="a page that only shows real content when logged in (default: Target account page)",
    )

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

            if args.auto_add_to_cart:
                _try_auto_add_to_cart(product, args.account, alerter)

        if args.once:
            return 0

        sleep_seconds = _next_sleep_seconds(
            config.check_interval_seconds, config.check_interval_jitter_seconds
        )
        time.sleep(sleep_seconds)


def _try_auto_add_to_cart(
    product: TrackedProduct, account_label: str, alerter: DiscordAlerter
) -> None:
    """Fire the auto-add-to-cart race once a restock is confirmed, and send a
    plain follow-up Discord message with the outcome either way.

    Only wired up for Target right now (see
    checkout/target_add_to_cart.py) -- any other retailer gets an honest
    "not supported yet" message instead of silently doing nothing.
    """
    if product.retailer != "target":
        logger.warning(
            "--auto-add-to-cart was requested for retailer '%s', but only "
            "'target' is supported right now -- skipping",
            product.retailer,
        )
        alerter.send_text(
            f"⚠️ --auto-add-to-cart isn't supported for '{product.retailer}' yet "
            f"(Target only, for now). You'll need to add {product.display_name} "
            "to your cart manually."
        )
        return

    logger.info("Restock confirmed for %s -- racing to add it to cart", product.display_name)
    result = add_to_cart_target(product.url, account_label=account_label)

    if result.success:
        logger.info("Added %s to cart in %d attempt(s)", product.display_name, result.attempts)
        alerter.send_text(
            f"🛒 Added **{product.display_name}** to your Target cart "
            f"(took {result.attempts} attempt(s)). Check out now 👉 https://www.target.com/cart"
        )
    else:
        logger.warning("Could not add %s to cart: %s", product.display_name, result.error)
        alerter.send_text(
            f"❌ Couldn't auto-add **{product.display_name}** to your Target cart "
            f"after {result.attempts} attempt(s): {result.error}. Jump in manually, fast 👉 {product.url}"
        )


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

    if args.command == "setup-session":
        capture_session(args.account, args.login_url)
        return 0

    if args.command == "import-cookies":
        import_cookies_from_file(args.account, args.file)
        return 0

    if args.command == "check-session":
        check_session(args.account, args.url)
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
