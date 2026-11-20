"""HA 驱动后端：给网关侧驱动（driver_name=ha）用的接口与逻辑。

背景（实测）：
- 网关只把 `property/down` 交给**驱动**，LINK 客户端收不到；
- `driverconfig/edit` 会校验驱动是否已安装，未安装返回 `500001 驱动不存在`；
- 所以驱动必须是网关侧的一个程序（有证书/驱动名），它把下行转成 HTTP 调本模块。

本模块提供三件事：
1. 设备清单：把 HA 全量设备 + 已注册映射 + 当前状态吐给驱动；
2. 下行执行：驱动收到 down 后调这里，转成 HA 服务调用；
3. 清单编辑：改名 / 换物模型 / 删除，直接写回网关并更新映射。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from . import bridge
from .const import (
    STORAGE_KEY,
    STORAGE_VERSION,
    TOPIC_FUNCTION_ADD,
    TOPIC_FUNCTION_DELETE,
    TOPIC_FUNCTION_EDIT,
    TOPIC_PROPERTY_UP,
)
from .protocol import LinkError
from .scan import async_scan

LOGGER = logging.getLogger(__name__)

_SUCCESS = ("0", "200", 0, 200)


def _ok(resp: dict[str, Any] | None) -> bool:
    return bool(resp) and resp.get("code") in _SUCCESS


from .driver_logic import service_calls  # noqa: E402  （纯逻辑放这里便于单测）

__all__ = ["service_calls"]

# ------------------------------------------------------------------ 设备清单
async def async_device_list(hass: HomeAssistant, coordinator: Any) -> dict[str, Any]:
    """给驱动用的清单：可同步设备 + 已注册映射 + 当前状态。"""
    from .provision import async_mapping

    mapping = await async_mapping(hass)
    by_entity = {eid: info for eid, info in mapping.items()}

    scan = await async_scan(hass)
    devices = []
    for device in scan.get("devices") or []:
        rows = []
        for row in device.get("functions") or []:
            eid = row.get("entity_id")
            state = hass.states.get(eid) if eid else None
            info = by_entity.get(eid) or {}
            rows.append({
                "entity_id": eid,
                "name": row.get("name"),
                "domain": row.get("domain"),
                "spk": info.get("spk") or row.get("spk"),
                "supported": row.get("supported"),
                "state": state.state if state else None,
                "attributes": dict(state.attributes) if state else {},
                "oid": info.get("oid"),
                "sid": info.get("sid"),
                "registered": bool(info.get("sid")),
            })
        devices.append({
            "device_id": device.get("device_id"),
            "name": device.get("name"),
            "model": device.get("model"),
            "domain": device.get("domain"),
            "area": device.get("area"),
            "integration": device.get("integration"),
            "existing": device.get("existing"),
            "functions": rows,
        })

    return {
        "devices": devices,
        "mapping": mapping,
        "stats": {
            "devices": len(devices),
            "functions": sum(len(d["functions"]) for d in devices),
            "registered": len(mapping),
        },
    }


async def async_drive_down(hass: HomeAssistant, coordinator: Any, objects: list[dict[str, Any]]) -> dict[str, Any]:
    """驱动收到的下行：按 sid 反查实体并执行对应的 HA 服务。"""
    from .provision import async_mapping

    mapping = await async_mapping(hass)
    sid_map = {info.get("sid"): eid for eid, info in mapping.items() if info.get("sid")}

    executed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for obj in objects:
        sid = str(obj.get("sid") or "")
        status = obj.get("status") or []
        entity_id = sid_map.get(sid)
        if not entity_id:
            skipped.append({"sid": sid, "reason": "sid 未映射到 HA 实体"})
            continue
        info = mapping.get(entity_id) or {}
        calls = service_calls({"entity_id": entity_id, "spk": info.get("spk")}, status)
        if not calls:
            skipped.append({"sid": sid, "reason": "该属性组无可执行动作"})
            continue
        for call in calls:
            try:
                await hass.services.async_call(
                    *call["service"].split("."), call["data"], blocking=False
                )
                executed.append({"sid": sid, "entity_id": entity_id, **call})
            except Exception as err:  # noqa: BLE001
                skipped.append({"sid": sid, "reason": f"{type(err).__name__}: {err}"})
    return {"executed": executed, "skipped": skipped}


# ------------------------------------------------------------------ 清单编辑
async def async_edit_function(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    entity_id: str,
    action: str,
    name: str | None = None,
    spk: str | None = None,
) -> dict[str, Any]:
    """编辑已注册功能：改名 / 换物模型 / 删除。"""
    from .provision import async_mapping

    mapping = await async_mapping(hass)
    info = mapping.get(entity_id)
    if not info:
        return {"ok": False, "error": "该实体尚未注册到网关"}

    sid = info["sid"]
    oid = info["oid"]
    gateway = coordinator.gateway
    result: dict[str, Any] = {"ok": False, "sid": sid, "entity_id": entity_id}

    if action == "delete":
        try:
            resp = await gateway.client.async_request(
                TOPIC_FUNCTION_DELETE.format(gw=gateway.oid), {"objects": [{"sid": sid}]}, timeout=10
            )
        except LinkError as err:
            return {"ok": False, "error": f"删除超时: {err}"}
        if not _ok(resp):
            return {"ok": False, "error": f"网关拒绝: {resp}"}
        mapping.pop(entity_id, None)
        await _save_mapping(hass, mapping)
        result.update(ok=True, action="delete")
        return result

    if action == "rename":
        if not name:
            return {"ok": False, "error": "缺少新名称"}
        resp = await _edit(hass, coordinator, sid, name=name)
        if not _ok(resp):
            return {"ok": False, "error": f"网关拒绝: {resp}"}
        info["name"] = name
        mapping[entity_id] = info
        await _save_mapping(hass, mapping)
        result.update(ok=True, action="rename", name=name)
        return result

    if action == "set_spk":
        new_spk = spk or ""
        if new_spk not in bridge.SPK_PROFILES:
            return {"ok": False, "error": f"未知物模型: {new_spk}"}
        # 网关不支持原地改属性集：删掉再建，sid 保持不变
        await gateway.client.async_request(
            TOPIC_FUNCTION_DELETE.format(gw=gateway.oid), {"objects": [{"sid": sid}]}, timeout=10
        )
        body = bridge.function_payload(oid, sid, new_spk, info.get("name") or entity_id, "")
        resp = await gateway.client.async_request(
            TOPIC_FUNCTION_ADD.format(gw=gateway.oid), body, timeout=12
        )
        if not _ok(resp):
            return {"ok": False, "error": f"重建被拒绝: {resp}"}
        info["spk"] = new_spk
        mapping[entity_id] = info
        await _save_mapping(hass, mapping)
        # 重新上报一次，恢复正常在线
        state = hass.states.get(entity_id)
        if state is not None:
            status = [{"key": "on_off", "value": "on" if state.state in ("on", "open") else "off"}]
            try:
                await gateway.client.async_request(
                    TOPIC_PROPERTY_UP.format(gw=gateway.oid),
                    bridge.up_body(sid, status),
                    timeout=6,
                )
            except LinkError:
                pass
        result.update(ok=True, action="set_spk", spk=new_spk)
        return result

    return {"ok": False, "error": f"未知操作: {action}"}


async def _edit(hass: HomeAssistant, coordinator: Any, sid: str, *, name: str) -> dict[str, Any] | None:
    gateway = coordinator.gateway
    try:
        return await gateway.client.async_request(
            TOPIC_FUNCTION_EDIT.format(gw=gateway.oid),
            {"objects": [{"sid": sid, "name": name[:32]}]},
            timeout=10,
        )
    except LinkError as err:
        LOGGER.debug("改名超时: %s", err)
        return None


async def _save_mapping(hass: HomeAssistant, mapping: dict[str, Any]) -> None:
    try:
        from homeassistant.helpers.storage import Store

        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        await store.async_save({"mapping": mapping})
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("保存映射失败: %s", err)


# ------------------------------------------------------------------ 驱动上报
async def async_push_driver_config(
    hass: HomeAssistant, coordinator: Any, driver_name: str = "ha"
) -> dict[str, Any]:
    """把 HA 的 sid 以驱动配置表的形式上报网关（驱动存在时才可能成功）。

    实测：驱动未安装时网关返回 500001「驱动不存在」——这一步就是验证驱动是否就绪的探针。
    """
    from .provision import async_mapping

    gateway = coordinator.gateway
    mapping = await async_mapping(hass)
    objects = []
    for entity_id, info in mapping.items():
        spk = info.get("spk") or ""
        objects.append({
            "src": driver_name,
            "spk": spk,
            "sid": info.get("sid"),
            "name": info.get("name") or entity_id,
            "type": "normal",
            "attrs": [
                {"name": a["key"], "type": 1}
                for a in bridge.SPK_PROFILES.get(spk, {}).get("attrs", [])[:4]
            ],
        })
    try:
        resp = await gateway.client.async_request(
            "/user/%s/custom/driverconfig/edit" % gateway.oid,
            {"driver_name": driver_name, "objects": objects},
            timeout=12,
        )
    except LinkError as err:
        return {"ok": False, "error": f"超时: {err}", "count": len(objects)}
    return {"ok": _ok(resp), "response": resp, "count": len(objects), "driver_name": driver_name}
