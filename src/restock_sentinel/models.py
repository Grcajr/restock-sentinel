"""Core data models shared across plugins, the database layer, and alerting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class TrackedProduct:
    """A product the bot has been configured to watch."""

    url: str
    retailer: str
    sku: str | None = None
    nickname: str | None = None
    max_price: float | None = None

    @property
    def display_name(self) -> str:
        return self.nickname or self.sku or self.url


@dataclass(slots=True)
class StockCheckResult:
    """The outcome of a single stock check against a retailer plugin."""

    product: TrackedProduct
    in_stock: bool
    price: float | None
    title: str | None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_status: str | None = None
    error: str | None = None

    @property
    def is_purchasable(self) -> bool:
        """True if the item is in stock, priced, and under any configured cap."""
        if not self.in_stock or self.price is None:
            return False
        if self.product.max_price is not None and self.price > self.product.max_price:
            return False
        return True
