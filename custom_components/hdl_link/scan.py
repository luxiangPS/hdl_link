"""扫描 HA 侧的设备与功能，生成可同步到 HDL 网关的候选清单。

设计原则（对齐产品目标）：
- 只读 HA 注册表与状态，不碰 HDL LINK 通信层；
- 一个 HA 设备 = 一个 HDL 物理模型（oid），设备下每个功能 = 一个功能点（sid）；
- 输出给 UI 直接可渲染、可直接勾选，且每个功能都带上「能不能写、写成什么」。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    PROVISIONABLE_SPK,
    SCAN_DOMAINS,
    SCAN_SKIP_DOMAINS,
    SPK_ATTRIBUTE_SCHEMA,
    SPK_PLATFORM,
    SYSTEM_INTEGRATIONS,
)

LOGGER = logging.getLogger(__name__)

# 可控域 -> 允许写入的 spk 候选（顺序即下拉展示顺序，第一个为默认）
CONTROL_SPK_OPTIONS: dict[str, list[str]] = {
    "light": ["light.dimming", "light.switch", "light.cct", "light.rgb", "light.rgbw"],
    "switch": ["other.common", "panel.socket"],
    "cover": ["curtain.trietex", "curtain.roller", "curtain.switch", "curtain.shades"],
    "climate": ["hvac.ac", "hvac.cac"],
    "fan": ["hvac.fan", "light.dimming"],
    "humidifier": ["hvac.airFresh", "other.common"],
    "lock": ["other.common"],
    "valve": ["other.common"],
    "siren": ["other.common"],
    "media_player": ["other.common"],
    "water_heater": ["other.common", "hvac.ac"],
    "vacuum": ["other.common", "light.switch"],
    "sensor": [
        "sensor.temperature", "sensor.humidity", "sensor.light", "sensor.co2",
        "sensor.pm25", "sensor.tvoc",
    ],
    "binary_sensor": [
        "sensor.pir", "sensor.doorwindow", "sensor.smoke", "sensor.gas",
        "sensor.water", "sensor.help", "sensor.dryContact",
    ],
}

# 仍未覆盖的域：照实标成「暂不支持」，不硬塞错误类型
UNSUPPORTED_DOMAINS: frozenset[str] = frozenset({"image_processing"})

# sensor 的 device_class -> spk
SENSOR_CLASS_SPK: dict[str, str] = {
    "temperature": "sensor.temperature",
    "humidity": "sensor.humidity",
    "illuminance": "sensor.light",
    "carbon_dioxide": "sensor.co2",
    "pm25": "sensor.pm25",
    "volatile_organic_compounds": "sensor.tvoc",
    "aqi": "sensor.pm25",
}

# binary_sensor 的 device_class -> spk
BINARY_CLASS_SPK: dict[str, str] = {
    "motion": "sensor.pir",
    "occupancy": "sensor.pir",
    "presence": "sensor.pir",
    "door": "sensor.doorwindow",
    "window": "sensor.doorwindow",
    "opening": "sensor.doorwindow",
    "garage_door": "sensor.doorwindow",
    "smoke": "sensor.smoke",
    "gas": "sensor.gas",
    "carbon_monoxide": "sensor.gas",
    "moisture": "sensor.water",
    "water": "sensor.water",
    "safety": "sensor.help",
    "problem": "sensor.help",
}

# 可控域的直接映射（没有更细能力判断时用）
_DOMAIN_SPK: dict[str, str] = {
    "lock": "other.common",
    "valve": "other.common",
    "siren": "other.common",
    "media_player": "other.common",
    "vacuum": "other.common",
    "water_heater": "other.common",
}


def spk_label(spk: str) -> str:
    """spk -> 中文展示名，供下拉与结果表使用。"""
    if not spk:
        return "暂不支持"
    info = SPK_PLATFORM.get(spk)
    return info[1] if info else spk


def _spk_options(domain: str, spk: str) -> list[dict[str, str]]:
    """该功能可选的 HDL 功能类型（只保留真正可写的）。"""
    ids = [s for s in CONTROL_SPK_OPTIONS.get(domain, []) if s in PROVISIONABLE_SPK]
    if spk and spk not in ids:
        ids.insert(0, spk)
    return [{"id": s, "label": spk_label(s)} for s in ids]


def _light_capability(state: Any) -> list[str]:
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
    """给出建议的 HDL 功能类型（spk）；返回空串表示暂不支持。"""
    if domain == "light":
        if "rgb" in capability:
            return "light.rgb"
        if "color_temp" in capability:
            return "light.cct"
        if "brightness" in capability:
            return "light.dimming"
        return "light.switch"
    if domain == "switch":
        return "panel.socket" if (device_class or "") == "outlet" else "other.common"
    if domain == "cover":
        if "position" in capability:
            return "curtain.trietex"
        return "curtain.switch"
    if domain == "climate":
        # 中央空调有阀门属性时用 cac，普通空调可控温度/模式用 ac
        return "hvac.ac"
    if domain == "fan":
        return "hvac.fan" if "percentage" in capability else "other.common"
    if domain == "humidifier":
        return "hvac.airFresh"
    if domain == "sensor":
        return SENSOR_CLASS_SPK.get(device_class or "", "")
    if domain == "binary_sensor":
        return BINARY_CLASS_SPK.get(device_class or "", "")
    return _DOMAIN_SPK.get(domain, "")


def _registry_oid(device: Any) -> str:
    """网关原生设备的 oid：identifiers 形如 (DOMAIN, "<gw_id>-<oid>")。"""
    for ident in getattr(device, "identifiers", ()) or ():
        try:
            if ident[0] == DOMAIN and "-" in str(ident[1]):
                return str(ident[1]).split("-", 1)[1]
        except (IndexError, TypeError):
            continue
    return ""


def _mapping_oid(rows: list[dict[str, Any]], mapping: dict[str, Any]) -> str:
    """本页同步过的设备：从映射表里取 oid（同一设备的功能共用一个 oid）。"""
    for row in rows:
        info = mapping.get(row["entity_id"]) or {}
        if info.get("oid"):
            return str(info["oid"])
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
    attrs = (state.attributes or {}) if state else {}
    device_class = (
        getattr(entity_entry, "device_class", None)
        or getattr(entity_entry, "original_device_class", None)
        or attrs.get("device_class")
    )
    unit = attrs.get("unit_of_measurement")

    if domain == "light":
        capability = _light_capability(state)
    elif domain == "cover":
        capability = _cover_capability(state)
    elif domain == "switch":
        capability = ["on_off"]
    elif domain == "climate":
        capability = ["on_off"]
        if "temperature" in attrs:
            capability.append("set_temp")
        if attrs.get("hvac_modes"):
            capability.append("mode")
        if attrs.get("fan_modes") or attrs.get("fan_mode"):
            capability.append("fan")
    elif domain == "fan":
        capability = ["on_off"] + (["percentage"] if "percentage" in attrs else [])
    elif domain in ("lock", "valve", "siren", "media_player", "vacuum", "water_heater"):
        capability = ["on_off"]
    else:
        capability = ["read"]

    if domain in UNSUPPORTED_DOMAINS:
        spk = ""
    else:
        spk = suggest_spk(domain, device_class, capability)

    friendly = attrs.get("friendly_name") or entity_entry.original_name or entity_id

    return {
        "entity_id": entity_id,
        "name": friendly,
        "domain": domain,
        "device_class": device_class,
        "unit": unit,
        "state": state.state if state else "unavailable",
        "kind": "sensor" if domain in ("sensor", "binary_sensor") else "control",
        "capability": capability,
        "suggested_spk": spk,
        "spk_label": spk_label(spk),
        "spk_options": _spk_options(domain, spk),
        "attributes": SPK_ATTRIBUTE_SCHEMA.get(spk, []),
        "supported": bool(spk),
        "supported_features": (
            int(attrs.get("supported_features", 0)) if state else 0
        ),
    }


async def async_scan(hass: HomeAssistant) -> dict[str, Any]:
    """扫描全部 HA 设备（排除 HDL LINK 自身与其网关），返回设备+功能清单。"""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    # 已写入网关的实体（本页面同步过）：以映射表为唯一可信来源，
    # 这样「移除出网关」之后重新扫描，同一行会自动变回可勾选状态。
    try:
        from .provision import async_mapping

        mapping: dict[str, Any] = await async_mapping(hass)
    except Exception:  # noqa: BLE001
        mapping = {}

    devices: list[dict[str, Any]] = []
    skipped_hdl = 0
    skipped_system = 0

    for device in dev_reg.devices.values():
        # HDL LINK 自己创建的设备：网关里已经有，标成「已接入」照常列出，但不可勾选修改
        is_existing = any(identifier[0] == DOMAIN for identifier in device.identifiers)
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

        integration = ""
        integration_domain = ""
        try:
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is not None:
                    integration = entry.title or entry.domain
                    integration_domain = entry.domain
                    break
        except Exception:  # noqa: BLE001
            pass

        is_system = integration_domain in SYSTEM_INTEGRATIONS
        # 功能级「已同步」：只认映射表，不认设备身份，移除后即可恢复可勾选
        for row in rows:
            if row["entity_id"] in mapping:
                row["existing"] = True
                row["supported"] = True
                row["mapped_sid"] = (mapping.get(row["entity_id"]) or {}).get("sid")
        if is_existing:
            # 网关原生设备（本集成从网关拉上来的镜像）：功能一律只读
            for row in rows:
                row["existing"] = True
                row["supported"] = True
                row["mirror"] = True
            skipped_hdl += 1
        any_existing = any(row.get("existing") for row in rows)
        # 本页写入过网关的设备：整卡按「已同步」处理（打勾锁定，可移除）
        device_existing = bool(is_existing) or any_existing
        supported_count = sum(1 for r in rows if r["supported"])

        # 系统集成且一个可写功能都没有的设备，直接不进列表（例如 Sun / Backup）
        if is_system and supported_count == 0 and not device_existing:
            skipped_system += 1
            continue

        # 可控功能优先，其次按域顺序、名称
        rows.sort(
            key=lambda r: (
                0 if r["supported"] else 1,
                SCAN_DOMAINS.index(r["domain"]) if r["domain"] in SCAN_DOMAINS else 99,
                r["name"],
            )
        )

        devices.append(
            {
                "device_id": device.id,
                "name": device.name_by_user or device.name or device.id,
                "model": device.model or "",
                "manufacturer": device.manufacturer or "",
                "domain": rows[0]["domain"],
                "area": _area_name(area_reg, device.area_id),
                "integration": integration,
                "integration_domain": integration_domain,
                "is_system": is_system,
                "existing": device_existing,
                "mirror": bool(is_existing),
                "gateway_oid": (
                    _registry_oid(device) if is_existing else _mapping_oid(rows, mapping)
                ),
                "functions": rows,
                "function_count": len(rows),
                "supported_count": supported_count,
                "control_count": sum(1 for r in rows if r["kind"] == "control"),
                "sensor_count": sum(1 for r in rows if r["kind"] == "sensor"),
            }
        )

    # 已在网关里的设备排最前，其次可接入的，其余按区域 + 名称
    devices.sort(
        key=lambda d: (
            0 if d["existing"] else 1,
            0 if d["supported_count"] else 1,
            d["area"] or "~",
            d["name"],
        )
    )

    # 同名功能检测：同一设备下多个功能重名（网关工程没起名、或混入别的工程时会出现）
    name_map: dict[tuple[str, str], list[str]] = {}
    for dev in devices:
        for fn_row in dev["functions"]:
            name_map.setdefault((dev["name"], fn_row["name"]), []).append(fn_row["entity_id"])
    duplicates = [
        {"device": key[0], "name": key[1], "entities": ids}
        for key, ids in name_map.items()
        if len(ids) > 1
    ]
    duplicates.sort(key=lambda x: (-len(x["entities"]), x["device"], x["name"]))

    total_functions = sum(len(d["functions"]) for d in devices)
    total_supported = sum(d["supported_count"] for d in devices)
    LOGGER.debug(
        "扫描完成：%d 设备 / %d 功能（可写 %d，网关已有 %d、系统 %d，同名组 %d）",
        len(devices), total_functions, total_supported, skipped_hdl, skipped_system, len(duplicates),
    )
    return {
        "devices": devices,
        "device_count": len(devices),
        "function_count": total_functions,
        "supported_count": total_supported,
        "existing_device_count": sum(1 for d in devices if d["existing"]),
        "skipped_hdl_devices": skipped_hdl,
        "skipped_system_devices": skipped_system,
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates[:30],
    }
