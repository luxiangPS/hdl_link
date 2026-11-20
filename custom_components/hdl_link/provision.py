"""把 HA 侧勾选的设备与功能写入 HDL 网关（物理模型 oid + 功能点 sid）。

2026-11-19/20 在 MGWIP.430（192.168.1.151:8586，oid 010105003DF6F100）实测确认的事实：

- device/add、function/attribute/add 走本地 TCP 8586 **确实落库**（code=0 + 回读确认）；
- oid 由客户端自带（16 位十六进制），sid = oid(16) + 12 位，网关原样保存；
- 网关不认 src / status / online 字段来判定在线：功能点建好就是 online=false；
- **让功能点上线的唯一办法是客户端主动上报属性**：
  /base/{gw}/thing/property/up + {"objects":[{"sid":..., "status":[{"key":...,"value":...}]}]}
  上报后回读 online=true 且 status 有值，property/down 才返回 code=0；
- 但 property/down 不会把下行帧推给 LINK 客户端（双连接验证），状态也不会被改写，
  所以桥接是**状态驱动**：HA → HDL 走得通（up 上报），HDL → HA 需要网关侧驱动。

因此本模块按"发报文 → 回读校验 → 上报在线 → 如实标注"实现，绝不把 code=0 当成功。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import bridge
from .const import (
    STORAGE_KEY,
    STORAGE_VERSION,
    TOPIC_DEVICE_ADD,
    TOPIC_DEVICE_LIST,
    TOPIC_FUNCTION_ADD,
    TOPIC_FUNCTION_LIST,
    TOPIC_FUNCTION_ATTR,
    TOPIC_PROPERTY_UP,
)
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)

_SUCCESS = ("0", "200", 0, 200)


def _ok(resp: dict[str, Any] | None) -> bool:
    if not resp:
        return False
    return resp.get("code") in _SUCCESS


def _objs(resp: dict[str, Any] | None) -> Any:
    if not resp:
        return []
    objs = resp.get("objects")
    if isinstance(objs, dict):
        objs = objs.get("objects") or []
    return objs if isinstance(objs, list) else []


async def _device_oids(gateway: Any) -> set[str]:
    resp = await gateway.client.async_request(TOPIC_DEVICE_LIST.format(gw=gateway.oid), timeout=12)
    return {str(d.get("oid")) for d in _objs(resp) if isinstance(d, dict) and d.get("oid")}


async def _functions(gateway: Any) -> dict[str, dict[str, Any]]:
    resp = await gateway.client.async_request(TOPIC_FUNCTION_LIST.format(gw=gateway.oid), timeout=15)
    return {
        str(f.get("sid")): f
        for f in _objs(resp)
        if isinstance(f, dict) and f.get("sid")
    }


async def _pull_state(hass: HomeAssistant, coordinator: Any, entity_id: str) -> list[dict[str, Any]]:
    """把 HA 当前状态按 spk 属性裁剪成 up 报文里的 status 数组。"""
    state = hass.states.get(entity_id)
    if state is None:
        return []
    spk = state.attributes.get("hdl_spk") or ""
    if not spk:
        return []
    return bridge.status_from_state(spk, state.state, dict(state.attributes))


async def async_report_up(gateway: Any, sid: str, status: list[dict[str, Any]]) -> bool:
    """上报一条功能点状态；这是让 sid 上线（online=true）的唯一手段。"""
    if not status:
        return False
    try:
        resp = await gateway.client.async_request(
            TOPIC_PROPERTY_UP.format(gw=gateway.oid),
            bridge.up_body(sid, status),
            timeout=8,
        )
    except LinkError as err:
        LOGGER.debug("property/up 失败 %s: %s", sid, err)
        return False
    # 该主题通常不回包，回包与否都不作为失败依据；以回读 online 为准
    LOGGER.debug("property/up %s -> %s", sid, resp)
    return True


async def async_provision(
    hass: HomeAssistant,
    coordinator: Any,
    selection: dict[str, Any],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """按勾选结果写入网关，返回计划、逐项状态与错误。

    selection: {"devices": [{"device_id","name","model","domain",
               "functions":[{"entity_id","name","spk"}]}]}
    """
    gateway = coordinator.gateway
    gw = gateway.oid
    devices_in = selection.get("devices") or []

    plan: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not devices_in:
        return {"dry_run": dry_run, "plan": [], "errors": ["没有勾选任何功能"], "warnings": []}

    try:
        oids_before = await _device_oids(gateway)
    except LinkError as err:
        return {
            "dry_run": dry_run,
            "plan": [],
            "errors": [f"读取网关设备列表失败（网关不可达？）：{err}"],
            "warnings": [],
        }

    funcs_before = await _functions(gateway) if not dry_run else {}

    for seq, device in enumerate(devices_in, start=1):
        dev_name = device.get("name") or device.get("device_id") or f"HA-{seq}"
        domain = (device.get("domain") or "").strip()
        device_key = str(device.get("device_id") or dev_name)
        oid = bridge.device_oid(device_key)

        dev_entry: dict[str, Any] = {
            "device_id": device.get("device_id"),
            "name": dev_name,
            "oid": oid,
            "applied": False,
            "functions": [],
        }

        if dry_run:
            dev_entry["oid"] = oid
        else:
            if oid in oids_before:
                dev_entry["applied"] = True
                dev_entry["note"] = "设备已存在（复用 oid）"
            else:
                payload = bridge.device_payload(oid, domain, device_key, dev_name)
                body = {"objects": [payload]}
                try:
                    resp = await gateway.client.async_request(
                        TOPIC_DEVICE_ADD.format(gw=gw), body, timeout=12
                    )
                except LinkError as err:
                    errors.append(f"设备「{dev_name}」新增超时: {err}")
                    plan.append(dev_entry)
                    continue

                if not _ok(resp):
                    errors.append(f"设备「{dev_name}」新增被拒绝: {resp}")
                    plan.append(dev_entry)
                    continue

                try:
                    oids_after = await _device_oids(gateway)
                except LinkError:
                    oids_after = set(oids_before)
                if oid in oids_after:
                    dev_entry["applied"] = True
                    oids_before.add(oid)
                else:
                    warnings.append(f"设备「{dev_name}」device/add 返回 Success，但回读未见该 oid")
                    dev_entry["applied"] = False

        for idx, func in enumerate(device.get("functions") or [], start=1):
            spk = (func.get("spk") or "").strip()
            func_name = func.get("name") or func.get("entity_id")
            entity_id = func.get("entity_id") or ""

            if not spk:
                spk = bridge.spk_for_entity(domain or entity_id.split(".")[0])
            if spk not in bridge.SPK_PROFILES:
                warnings.append(f"「{dev_name}」跳过功能「{func_name}」：未知 HDL 类型 {spk}")
                continue

            sid = bridge.function_sid(oid, idx)
            item = {
                "entity_id": entity_id,
                "name": func_name,
                "spk": spk,
                "sid": sid,
                "ok": False,
            }

            if dry_run:
                item["ok"] = True
                item["note"] = "演练：未写网关"
                dev_entry["functions"].append(item)
                continue

            if not dev_entry["applied"]:
                item["note"] = "设备未建立，功能未下发"
                dev_entry["functions"].append(item)
                continue

            if sid in funcs_before:
                item["ok"] = True
                item["note"] = "sid 已存在（复用）"
            else:
                body = bridge.function_payload(oid, sid, spk, func_name, domain)
                try:
                    resp = await gateway.client.async_request(
                        TOPIC_FUNCTION_ADD.format(gw=gw), body, timeout=12
                    )
                    if not _ok(resp):
                        item["note"] = f"网关拒绝: {str(resp)[:120]}"
                        errors.append(f"功能「{func_name}」新增失败: {resp}")
                        dev_entry["functions"].append(item)
                        continue
                    funcs_before.update(await _functions(gateway))
                    if sid in funcs_before:
                        item["ok"] = True
                        item["note"] = "回读确认"
                    else:
                        item["note"] = "返回 Success 但功能列表未出现"
                        warnings.append(f"功能「{func_name}」未真正写入网关")
                        dev_entry["functions"].append(item)
                        continue
                except LinkError as err:
                    item["note"] = str(err)[:200]
                    errors.append(f"功能「{func_name}」新增超时: {err}")
                    dev_entry["functions"].append(item)
                    continue

            # 关键一步：上报一次属性，让 sid 上线（网关只认这条）
            if entity_id:
                status = await _pull_state(hass, coordinator, entity_id)
                if status:
                    await async_report_up(gateway, sid, status)
                    item["report"] = len(status)

            dev_entry["functions"].append(item)

        plan.append(dev_entry)

    if not dry_run:
        await _async_save_mapping(hass, plan)

    return {
        "dry_run": dry_run,
        "plan": plan,
        "errors": errors,
        "warnings": warnings,
        "created_devices": sum(1 for p in plan if p.get("applied")),
        "created_functions": sum(1 for p in plan for f in p["functions"] if f.get("ok")),
    }


async def async_sync_states(hass: HomeAssistant, coordinator: Any) -> int:
    """把已映射实体的状态推给网关（heartbeat 用：维持 online + 状态同步）。"""
    mapping = await async_mapping(hass)
    if not mapping:
        return 0
    gateway = coordinator.gateway
    pushed = 0
    for entity_id, info in mapping.items():
        sid = info.get("sid")
        if not sid:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        spk = info.get("spk") or ""
        status = []
        for attr in bridge.SPK_PROFILES.get(spk, {}).get("attrs", []):
            key = attr["key"]
            raw = None
            if key == "on_off":
                raw = state.state
            elif key in ("brightness", "cct", "rgb", "rgbw"):
                raw = state.attributes.get(key)
            elif key == "percent":
                raw = state.attributes.get("current_position")
            elif key in ("temperature", "humidity", "illuminance", "co2", "pm25",
                         "people_status", "door_status"):
                raw = state.state
            if raw is None:
                continue
            value = bridge.to_hdl_value(spk, key, raw)
            if value is not None:
                status.append({"key": key, "value": value})
        if status and await async_report_up(gateway, sid, status):
            pushed += 1
    return pushed


async def async_read_online(gateway: Any, sid: str) -> dict[str, Any]:
    """回读单个功能点的 online/status，用于确认上报是否生效。"""
    try:
        resp = await gateway.client.async_request(
            TOPIC_FUNCTION_ATTR.format(gw=gateway.oid), {"objects": [{"sid": sid}]}, timeout=8
        )
    except LinkError:
        return {}
    objs = _objs(resp)
    return objs[0] if objs else {}


async def _async_save_mapping(hass: HomeAssistant, plan: list[dict[str, Any]]) -> None:
    """把 entity_id -> (oid, sid) 落盘，供后续核对、状态同步与删除。"""
    try:
        from homeassistant.helpers.storage import Store

        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        data = await store.async_load() or {}
        mapping = data.get("mapping", {}) if isinstance(data, dict) else {}
        for dev in plan:
            for func in dev.get("functions") or []:
                if func.get("entity_id") and func.get("ok"):
                    mapping[func["entity_id"]] = {
                        "oid": dev.get("oid"),
                        "sid": func.get("sid"),
                        "spk": func.get("spk"),
                        "device_id": dev.get("device_id"),
                        "name": func.get("name"),
                    }
        await store.async_save({"mapping": mapping})
        LOGGER.info("已保存 %d 条 entity→sid 映射", len(mapping))
    except Exception as err:  # noqa: BLE001 - 落盘失败不能影响下发结果
        LOGGER.warning("保存映射失败: %s", err)


async def async_mapping(hass: HomeAssistant) -> dict[str, Any]:
    """读取已保存的映射。"""
    try:
        from homeassistant.helpers.storage import Store

        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        data = await store.async_load() or {}
        return data.get("mapping", {}) if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def async_entity_for_sid(hass: HomeAssistant, sid: str) -> dict[str, Any] | None:
    """sid 反查实体：网关下发的 down 里只有 sid，用它找 entity_id / spk。"""
    mapping = await async_mapping(hass)
    for entity_id, info in mapping.items():
        if str(info.get("sid") or "") == sid:
            return {"entity_id": entity_id, "spk": info.get("spk") or "",
                    "oid": info.get("oid"), "name": info.get("name")}
    return None
