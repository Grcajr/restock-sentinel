"""Shared helper for the not-yet-implemented retailer stubs.

Not a plugin itself (no ``name``, not registered) — just factors out the
"NotImplementedError with a helpful message" behaviour so each stub file
stays a one-liner plus a docstring explaining what real work is needed.
"""

from __future__ import annotations

from restock_sentinel.models import StockCheckResult, TrackedProduct
from restock_sentinel.plugins.base import RetailerPlugin


class UnimplementedPlugin(RetailerPlugin):
    """Base for retailers that are registered but not yet implemented.

    Kept in the registry (rather than left out entirely) so ``--list-retailers``
    and the CLI's error messages show the full intended scope of the project,
    and so implementing one is just filling in ``check_stock`` in an existing
    file instead of wiring up a new plugin from scratch.
    """

    def check_stock(self, product: TrackedProduct) -> StockCheckResult:
        return StockCheckResult(
            product=product,
            in_stock=False,
            price=None,
            title=None,
            error=f"{self.name} plugin is not implemented yet",
        )
