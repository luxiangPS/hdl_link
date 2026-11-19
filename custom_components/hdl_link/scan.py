"""扫描 HA 侧的设备与功能，生成可同步到 HDL 网关的候选清单。

设计原则：
- 不碰 HDL LINK 已有的通信层，只读 HA 的注册表与状态；
- 一个 HA 设备 = 一个 HDL 物理模型（oid），设备下的每个功能 = 一个功能点（sid）；
- 输出结构直接给 UI 勾选用，也直接给 provisioner 写入用。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SCAN_DOMAINS, SCAN_SKIP_DOMAINS, SPK_ATTRIBUTE_SCHEMA

LOGGER = logging.getLogger(__name__)

# HA 域 -> 候选 spk 列表（第一个是默认建议）
DOMAIN_SPK_CANDIDATES: dict[str, list[str]] = {
    "light": ["light.switch", "light.dimming", "light.cct", "light.rgb", "light.rgbw"],
    "switch": ["other.common", "panel.socket"],
    "cover": ["curtain.switch", "curtain.trietex", "curtain.roller"],
    "sensor": ["sensor.temperature", "sensor.humidity", "sensor.light", "sensor.co2", "sensor.pm25"],
    "binary_sensor": ["sensor.pir", "sensor.doorwindow"],
}

# sensor 的 device_class -> spk
SENSOR_CLASS_SPK: dict[str, str] = {
    "temperature": "sensor.temperature",
    "humidity": "sensor.humidity",
    "illuminance": "sensor.light",
    "carbon_dioxide": "sensor.co2",
    "pm25": "sensor.pm25",
}

# binary_sensor 的 device_class -> spk
BINARY_CLASS_SPK: dict[str, str] = {
    "motion": "sensor.pir",
    "occupancy": "sensor.pir",
    "presence": "sensor.pir",
    "door": "sensor.doorwindow",
    "window": "sensor.doorwindow",
    "opening": "sensor.doorwindow",
}


def _light_capability(state: Any) -> list[str]:
    """按当前状态属性推断灯的能力。"""
    attrs = getattr(state, "attributes", {}) or {}
    caps = ["on_off"]
    if "brightness" in attrs:
        caps.append("brightness")
    if "color_temp_kelvin" in attrs or "color_temp" in attrs:
        caps.append("color_temp")
    if "rgb_color" in attrs or "hs_color" in attrs:
        caps.append("rgb")
    return caps


def _cover_capability(state: Any) -> list[str]:
    attrs = getattr(state, "attributes", {}) or {}
    caps = ["open", "close", "stop"]
    if "current_position" in attrs:
        caps.append("position")
    return caps


def suggest_spk(domain: str, device_class: str | None, capability: list[str]) -> str:
    """给出建议的 HDL 功能类型（spk）。"""
    if domain == "light":
        if "rgb" in capability and "color_temp" in capability:
            return "light.rgb"
        if "rgb" in capability:
            return "light.rgb"
        if "color_temp" in capability:
            return "light.cct"
        if "brightness" in capability:
            return "light.dimming"
        return "light.switch"
    if domain == "switch":
        return "other.common"
    if domain == "cover":
        return "curtain.trietex" if "position" in capability else "curtain.switch"
    if domain == "sensor":
        return SENSOR_CLASS_SPK.get(device_class or "", "sensor.temperature")
    if domain == "binary_sensor":
        return BINARY_CLASS_SPK.get(device_class or "", "sensor.pir")
    return ""


def _entries_for_device(ent_reg: Any, device_id: str) -> list[Any]:
    """兼容不同 HA 版本：模块级函数 / 注册表方法。"""
    fn = getattr(er, "async_entries_for_device", None)
    if fn is not None:
        try:
            return fn(ent_reg, device_id, include_disabled_entities=False)
        except TypeError:
            return fn(ent_reg, device_id)
    method = getattr(ent_reg, "async_entries_for_device", None)
    if method is not None:
        try:
            return method(device_id, include_disabled_entities=False)
        except TypeError:
            return method(device_id)
    return []


def _area_name(area_reg: Any, area_id: str | None) -> str:
    """取区域名，兼容两种访问方式。"""
    if not area_id:
        return ""
    for target in (area_reg, ar):
        fn = getattr(target, "async_get_area", None)
        if fn is None:
            continue
        try:
            area = fn(area_id)
        except TypeError:
            continue
        if area is not None:
            return getattr(area, "name", "") or ""
    return ""


def _entity_row(
    hass: HomeAssistant,
    entity_entry: er.RegistryEntry,
) -> dict[str, Any] | None:
    """把一个 HA 实体整理成一行功能候选。"""
    entity_id = entity_entry.entity_id
    domain = entity_id.split(".", 1)[0]
    if domain in SCAN_SKIP_DOMAINS or domain not in SCAN_DOMAINS:
        return None

    state = hass.states.get(entity_id)
    device_class = (
        getattr(entity_entry, "device_class", None)
        or getattr(entity_entry, "original_device_class", None)
        or ((state.attributes or {}).get("device_class") if state else None)
    )
    unit = (state.attributes or {}).get("unit_of_measurement") if state else None

    if domain == "light":
        capability = _light_capability(state)
    elif domain == "cover":
        capability = _cover_capability(state)
    elif domain == "switch":
        capability = ["on_off"]
    else:
        capability = ["read"]

    spk = suggest_spk(domain, device_class, capability)
    friendly = (
        (state.attributes or {}).get("friendly_name") if state else None
    ) or entity_entry.original_name or entity_id

    return {
        "entity_id": entity_id,
        "name": friendly,
        "domain": domain,
        "device_class": device_class,
        "unit": unit,
        "state": state.state if state else "unavailable",
        "supported_features": (
            int((state.attributes or {}).get("supported_features", 0)) if state else 0
        ),
        "capability": capability,
        "suggested_spk": spk,
        "spk_options": DOMAIN_SPK_CANDIDATES.get(domain, [spk] if spk else []),
        "attributes": SPK_ATTRIBUTE_SCHEMA.get(spk, []),
        "writable": domain in ("light", "switch", "cover"),
    }


async def async_scan(hass: HomeAssistant) -> dict[str, Any]:
    """扫描全部 HA 设备（排除 HDL LINK 自身与其网关），返回设备+功能清单。"""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    devices: list[dict[str, Any]] = []
    skipped_hdl = 0

    for device in dev_reg.devices.values():
        # 排除 HDL LINK 自己创建的设备（它们本来就已在网关里）
        if any(identifier[0] == DOMAIN for identifier in device.identifiers):
            skipped_hdl += 1
            continue
        if device.disabled:
            continue

        try:
            entries = _entries_for_device(ent_reg, device.id)
            rows = [row for row in (_entity_row(hass, e) for e in entries) if row]
        except Exception as err:  # noqa: BLE001 - 单台设备异常不影响整体扫描
            LOGGER.warning("扫描设备 %s 失败: %s", device.id, err)
            continue
        if not rows:
            continue

        # 功能按 SCAN_DOMAINS 顺序排
        rows.sort(key=lambda r: (SCAN_DOMAINS.index(r["domain"]) if r["domain"] in SCAN_DOMAINS else 99,
                                 r["name"]))

        integration = ""
        try:
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is not None:
                    integration = entry.title or entry.domain
                    break
        except Exception:  # noqa: BLE001
            integration = ""

        devices.append(
            {
                "device_id": device.id,
                "name": device.name_by_user or device.name or device.id,
                "model": device.model or "",
                "manufacturer": device.manufacturer or "",
                "area": _area_name(area_reg, device.area_id),
                "integration": integration,
                "functions": rows,
            }
        )

    devices.sort(key=lambda d: (d["area"] or "~", d["name"]))

    total_functions = sum(len(d["functions"]) for d in devices)
    LOGGER.debug(
        "扫描完成：%d 个设备 / %d 个功能（跳过 HDL 自有设备 %d 个）",
        len(devices), total_functions, skipped_hdl,
    )
    return {
        "devices": devices,
        "device_count": len(devices),
        "function_count": total_functions,
        "skipped_hdl_devices": skipped_hdl,
    }
