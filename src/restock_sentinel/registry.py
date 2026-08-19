"""Plugin registry: maps a retailer's short name to its plugin class.

New plugins register themselves with the ``@register_plugin`` decorator at
import time. :func:`load_builtin_plugins` imports every module under
``restock_sentinel.plugins`` so decorators run and the registry is populated,
without every caller needing to know the full list of plugin modules.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable

from restock_sentinel.plugins.base import RetailerPlugin

_REGISTRY: dict[str, type[RetailerPlugin]] = {}


def register_plugin(cls: type[RetailerPlugin]) -> type[RetailerPlugin]:
    """Class decorator that registers a :class:`RetailerPlugin` subclass."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define `name` before registering")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"A plugin is already registered under name {cls.name!r}")
    _REGISTRY[cls.name] = cls
    return cls


def get_plugin(name: str) -> type[RetailerPlugin]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "(none loaded)"
        raise KeyError(
            f"No plugin registered as {name!r}. Available: {available}"
        ) from exc


def list_plugins() -> Iterable[str]:
    return sorted(_REGISTRY)


def load_builtin_plugins() -> None:
    """Import every module in ``restock_sentinel.plugins`` to trigger registration."""
    import restock_sentinel.plugins as plugins_pkg

    for module_info in pkgutil.iter_modules(plugins_pkg.__path__):
        if module_info.name in {"base"}:
            continue
        importlib.import_module(f"restock_sentinel.plugins.{module_info.name}")
