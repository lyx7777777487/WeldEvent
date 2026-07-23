"""Pytest 全局配置 - 把 WeldEvent 加入 sys.path + 共享 fixtures.

WeldEvent 代码在 /Users/liuyixuan/WeldEvent, 本测试模块在 Codex workspace.
conftest 自动把 WeldEvent 加到 sys.path, 让 import cognitiveplane / controlplane 可用.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── sys.path 注入 ──
_WELDEVENT_ROOT = Path(os.environ.get(
    "WELDEVENT_ROOT", "/Users/liuyixuan/WeldEvent"
))
if str(_WELDEVENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_WELDEVENT_ROOT))

# 确保能 import
try:
    import cognitiveplane  # noqa: F401
    import controlplane  # noqa: F401
    _WELDEVENT_OK = True
except ImportError:
    _WELDEVENT_OK = False

import pytest

from catalog_test.catalog_map import (
    ALL_ITEMS, TESTABLE_ITEMS, L1_ITEMS, L2_ITEMS, CatalogItem,
)


# ── fixtures ──

@pytest.fixture
def catalog_items():
    """全部清单条目."""
    return ALL_ITEMS


@pytest.fixture
def testable_items():
    """仅可测条目 (✅ + 🆕)."""
    return TESTABLE_ITEMS


@pytest.fixture
def l1_items():
    """L1 工具级可测条目 (有 l1_action)."""
    return L1_ITEMS


@pytest.fixture
def l2_items():
    """L2 端到端可测条目."""
    return L2_ITEMS


@pytest.fixture
def weldevent_ok():
    """WeldEvent 代码是否可 import (skip 标记用)."""
    if not _WELDEVENT_OK:
        pytest.skip("WeldEvent 代码不可 import (设 WELDEVENT_ROOT 环境变量)")
    return True
