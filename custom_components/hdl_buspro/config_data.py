"""配置合并：选项（Options）优先于首次配置（data）。"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry


def merged_config(entry: ConfigEntry) -> dict[str, Any]:
    """把 data 与 options 合并；选项里改的通道编排会覆盖首次配置。"""
    return {**entry.data, **entry.options}
