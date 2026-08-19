# restock-sentinel

A modular, plugin-based restock/stock-alert bot for e-commerce sites. It
polls a product page, detects when an out-of-stock item comes back, sends a
Discord alert, and can optionally drive a real browser through a **dry-run
checkout** (add-to-cart → checkout, stopping short of payment) to prove the
automation can act on the alert fast enough to matter.

Built to be extended: adding a new retailer is writing one class, not
touching the scheduler, database, or alerting code.

## Why this exists

Limited-stock items (restocks, drops, collectibles) sell out in minutes.
Checking manually doesn't scale past one or two products, and every
retailer's site is different enough that a single scraper doesn't work
everywhere. This project's actual engineering problem isn't "scrape a
webpage" — it's building an architecture where each retailer's quirks are
isolated behind one interface, so the system keeps working (and stays
testable) as retailers are added.

## Architecture

```
                     ┌─────────────────┐
                     │       CLI        │
                     │  (cli.py)         │
                     └────────┬─────────┘
                              │
                 ┌────────────┼─────────────┐
                 ▼            ▼              ▼
          ┌───────────┐ ┌───────────┐ ┌──────────────┐
          │  registry │ │    db     │ │    alerts     │
          │  (plugin  │ │ (SQLite,  │ │  (Discord      │
          │  lookup)  │ │  history) │ │   webhook)     │
          └─────┬─────┘ └───────────┘ └──────────────┘
                │
                ▼
      ┌───────────────────┐
      │  RetailerPlugin    │  <- abstract base, one method: check_stock()
      │  (plugins/base.py) │
      └─────────┬──────────┘
                │ implemented by
   ┌────────────┼─────────────┬───────────┬──────────┬────────────────┐
   ▼            ▼              ▼           ▼          ▼                ▼
amazon_mx    walmart      sams_club     target      costco      pokemon_center
(working)    (stub)        (stub)       (stub)      (stub)          (stub)
                              │
                              ▼
                    ┌───────────────────┐
                    │ checkout/          │
                    │ playwright_checkout │  <- dry-run add-to-cart -> checkout
                    └───────────────────┘
```

Each plugin implements one method — `check_stock(product) -> StockCheckResult`
— and registers itself with a decorator. The CLI, database layer, and alerting
code never import a specific retailer; they only know about the
`RetailerPlugin` interface and the registry. See `plugins/walmart.py`,
`plugins/target.py`, etc. for what "planned but not yet implemented" looks
like, including notes on what each retailer's real integration would need.

## Status

| Retailer         | Status        |
|-------------------|---------------|
| Amazon.mx          | ✅ Implemented |
| Walmart             | 🔜 Stubbed     |
| Sam's Club          | 🔜 Stubbed     |
| Target               | 🔜 Stubbed     |
| Costco               | 🔜 Stubbed     |
| Pokémon Center      | 🔜 Stubbed     |

## Tech stack

- **Python 3.11+**, stdlib `sqlite3` for persistence (no external DB to run)
- **requests** + **BeautifulSoup** for stock checks
- **Playwright** for the dry-run checkout browser automation
- **Discord webhooks** for alerting
- **pytest** for tests, **ruff** for linting, **GitHub Actions** for CI

## Setup

```bash
git clone https://github.com/USERNAME/restock-sentinel.git
cd restock-sentinel

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
playwright install chromium

cp .env.example .env
# then edit .env: set DISCORD_WEBHOOK_URL at minimum
```

## Usage

```bash
# See which retailers are registered
restock-sentinel list-retailers

# Watch a product, checking every CHECK_INTERVAL_SECONDS (from .env)
restock-sentinel watch \
  --retailer amazon_mx \
  --url "https://www.amazon.com.mx/dp/EXAMPLE" \
  --nickname "Example Widget" \
  --max-price 899.00

# Check once and exit (useful for cron / testing)
restock-sentinel watch --retailer amazon_mx --url "https://www.amazon.com.mx/dp/EXAMPLE" --once
```

When a tracked product transitions from out-of-stock to in-stock (and under
`--max-price`, if set), a Discord alert fires. Every check is logged to
SQLite (`data/restock_sentinel.db` by default) so you can audit history.

## Running tests

```bash
pytest
ruff check .
```

## Responsible use

This project checks publicly viewable product pages and, optionally, walks
through checkout **without ever submitting payment**. It does not bypass
logins, CAPTCHAs, or paywalls, and it isn't built to place unattended real
purchases. Scraping frequency should respect each site's terms of service
and `robots.txt` — the default 60-second interval is deliberately
conservative. You're responsible for how you configure and run it.

## Roadmap

- Implement Walmart, Target, Sam's Club, Costco, and Pokémon Center plugins
  (see the TODO docstring in each stub for retailer-specific notes)
- Multi-product watch lists from a config file instead of one `--url` per run
- Web dashboard over the existing SQLite history
- Optional SMS/email alert backends alongside Discord
