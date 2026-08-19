from __future__ import annotations

import pytest

from restock_sentinel.registry import get_plugin, list_plugins, load_builtin_plugins


def test_load_builtin_plugins_registers_all_retailers():
    load_builtin_plugins()
    names = set(list_plugins())
    assert names == {
        "amazon_mx",
        "walmart",
        "sams_club",
        "target",
        "costco",
        "pokemon_center",
    }


def test_get_plugin_returns_class_by_name():
    load_builtin_plugins()
    plugin_cls = get_plugin("amazon_mx")
    assert plugin_cls.name == "amazon_mx"


def test_get_plugin_unknown_name_raises_with_helpful_message():
    load_builtin_plugins()
    with pytest.raises(KeyError, match="amazon_mx"):
        get_plugin("not_a_real_retailer")
