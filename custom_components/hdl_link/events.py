"""HDL 功能点 → HA 直接绑定（不需要场景中转）。

把任意一个 HDL 功能点的 SID 直接绑到 HA 实体：

1. ``mode="follow"``（默认，推荐）：功能点状态变化原样映射成 HA 服务调用，
   开/关、亮度、色温、RGB、窗帘位置、空调温度/模式/风速跟着走；
2. ``mode="action"``：状态变化只触发一个动作（toggle / turn_on / open / run …），
   适合面板按键当遥控器。

上行来源（实机验证过的两条）：
- ``/base/{gw}/thing/property/up``：功能点状态上报（本地加密），
  面板按下后由网关推给 LINK 客户端；
- ``/user/{gw}/custom/scene|logic/status/up``：场景/逻辑执行事件。

判断依据只有 SID：命中绑定表就下发 HA，命不中只记录到 ``seen``。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .driver_logic import normalize_status, service_calls, should_fire

LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "hdl_link.bindings"
STORAGE_VERSION = 1

ACTION_SERVICES = {
    "turn_on": "homeassistant.turn_on",
    "turn_off": "homeassistant.turn_off",
    "toggle": "homeassistant.toggle",
    "open": "cover.open_cover",
    "close": "cover.close_cover",
    "run": "script.turn_on",
}
TRIGGERS = ("any", "on", "off")
MODES = ("follow", "action")


async def _store(hass: HomeAssistant):
    from homeassistant.helpers.storage import Store

    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


def _blank() -> dict[str, Any]:
    return {"sids": {}, "seen": [], "state": {}}


async def async_load(hass: HomeAssistant) -> dict[str, Any]:
    try:
        data = await (await _store(hass)).async_load()
    except Exception:  # noqa: BLE001
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    # 兼容 2.7.0 的旧结构：场景映射 -> 功能点绑定
    if "scenes" in data and "sids" not in data:
        data["sids"] = {
            sid: {**rule, "mode": "action"}
            for sid, rule in (data.get("scenes") or {}).items()
        }
        data.pop("scenes", None)
    data.setdefault("sids", {})
    data.setdefault("seen", [])
    data.setdefault("state", {})
    return data


async def async_save(hass: HomeAssistant, data: dict[str, Any]) -> None:
    try:
        await (await _store(hass)).async_save(data)
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("保存功能绑定失败: %s", err)


async def async_set_mapping(
    hass: HomeAssistant,
    sid: str,
    *,
    entity_id: str | None = None,
    mode: str = "follow",
    action: str = "toggle",
    trigger: str = "any",
    spk: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """登记/更新一条 功能点 sid → HA 绑定；entity_id 为空表示删除。"""
    data = await async_load(hass)
    if not sid:
        return {"ok": False, "error": "缺少 sid"}
    if not entity_id:
        rule = data["sids"].pop(sid, None)
        await async_save(hass, data)
        return {"ok": True, "action": "delete", "sid": sid, "removed": bool(rule)}
    if mode not in MODES:
        return {"ok": False, "error": f"未知模式: {mode}"}
    if mode == "action" and action not in ACTION_SERVICES:
        return {"ok": False, "error": f"未知动作: {action}"}
    if trigger not in TRIGGERS:
        return {"ok": False, "error": f"未知触发条件: {trigger}"}
    if mode == "follow" and not spk:
        spk = ""
    data["sids"][sid] = {
        "entity_id": entity_id,
        "mode": mode,
        "action": action if mode == "action" else "",
        "trigger": trigger,
        "spk": spk or data["sids"].get(sid, {}).get("spk") or "",
        "name": name or data["sids"].get(sid, {}).get("name") or "",
    }
    await async_save(hass, data)
    return {"ok": True, "action": "set", "sid": sid, "mapping": data["sids"][sid]}


def _remember(data: dict[str, Any], sid: str, values: dict[str, str], kind: str) -> bool:
    """把收到过的功能点记进 seen（前端下拉挑 sid 用），返回是否有变化。"""
    seen = data["seen"]
    snapshot = ";".join(f"{k}={v}" for k, v in sorted(values.items()))
    for item in seen:
        if item.get("sid") == sid:
            item["last_status"] = snapshot or item.get("last_status") or ""
            item["kind"] = kind or item.get("kind") or ""
            return False
    seen.append({"sid": sid, "name": "", "kind": kind, "last_status": snapshot})
    return True


async def async_handle(
    hass: HomeAssistant,
    objects: list[dict[str, Any]],
    *,
    kind: str = "function",
    own_sids: set[str] | None = None,
) -> dict[str, Any]:
    """处理一批上行 objects：先记录，再按 sid 直连绑定执行。"""
    data = await async_load(hass)
    own = own_sids or set()
    executed: list[dict[str, Any]] = []
    dirty = False
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        sid = str(obj.get("sid") or "")
        if not sid:
            continue
        status = normalize_status(obj.get("status"))
        values = {item["key"]: item["value"] for item in status}
        if sid in own:
            # 我们自己注册进网关的 HA 设备：状态由协调器刷新，不参与绑定判断
            continue
        if _remember(data, sid, values, kind):
            dirty = True
        if not status:
            continue

        rule = data["sids"].get(sid)
        if not rule:
            continue

        key = ";".join(sorted(f"{k}={v}" for k, v in values.items()))
        if data["state"].get(sid) == key:
            # 同一份 status 已经下发过，重复上报（含自己发 up 的回环）不再执行
            continue
        data["state"][sid] = key
        if not should_fire(rule.get("trigger") or "any", values, status):
            continue

        entity_id = rule.get("entity_id") or ""
        if not entity_id:
            continue

        if rule.get("mode") == "action":
            service = ACTION_SERVICES.get(rule.get("action") or "toggle")
            calls = [{"service": service, "data": {"entity_id": entity_id}}] if service else []
        else:
            calls = service_calls({"entity_id": entity_id, "spk": rule.get("spk") or ""}, status)

        for call in calls:
            try:
                await hass.services.async_call(
                    *call["service"].split("."), call["data"], blocking=False
                )
                executed.append({"sid": sid, **call})
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("下发失败 %s → %s: %s", sid, call["service"], err)

        if executed:
            LOGGER.info("HDL 功能点 %s → %s", sid, rule.get("entity_id"))

    if dirty:
        await async_save(hass, data)
    return {"executed": executed}


async def async_bindings(hass: HomeAssistant) -> dict[str, Any]:
    """给前端/脚本看：绑定表 + 收到过的 sid。"""
    data = await async_load(hass)
    return {
        "sids": data.get("sids") or {},
        "seen": data.get("seen") or [],
        "modes": list(MODES),
        "actions": list(ACTION_SERVICES),
        "triggers": list(TRIGGERS),
    }
