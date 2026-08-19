"""Base interface every retailer plugin must implement.

Adding support for a new retailer means writing one class that implements
``check_stock`` and registering it — the scheduler, database layer, and
alerting code never need to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from restock_sentinel.models import StockCheckResult, TrackedProduct

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class RetailerPlugin(ABC):
    """Base class for a single retailer integration.

    Subclasses must set ``name`` (a short, unique, lowercase identifier used
    in the CLI, database, and registry — e.g. ``"amazon_mx"``) and implement
    :meth:`check_stock`.
    """

    name: str = ""

    def __init__(self, *, session: requests.Session | None = None) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must define a non-empty `name`")
        self.session = session or self._build_default_session()

    def _build_default_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            }
        )
        return session

    @abstractmethod
    def check_stock(self, product: TrackedProduct) -> StockCheckResult:
        """Check whether ``product`` is currently in stock and purchasable.

        Implementations should catch their own network/parsing errors and
        return a :class:`StockCheckResult` with ``error`` set rather than
        raising, so a single bad request never crashes the watch loop for
        every other tracked product.
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name!r}>"
