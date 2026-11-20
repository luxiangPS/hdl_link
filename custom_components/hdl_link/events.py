"""HDL 事件 → HA 动作：按 SID 判断是不是 HA 生态设备，直接下发控制。

网关实测行为：
- 面板直接控一个"无驱动"的 sid 时，网关内部转发失败，**不会**向 LINK 客户端广播任何帧；
- 面板键绑定场景时，网关会推 `/custom/scene/status/up`（加密）→ 可被集成收到。

所以这里的"按 SID 判断"落在**场景/逻辑 SID** 上：
  scene sid ↔ HA 实体动作 的映射表存在本地，事件一到就直接调 HA 服务。
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .driver_logic import service_calls

LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "hdl_link.events"
STORAGE_VERSION = 1

ACTION_SERVICES = {
    "turn_on": "homeassistant.turn_on",
    "turn_off": "homeassistant.turn_off",
    "toggle": "homeassistant.toggle",
    "open": "cover.open_cover",
    "close": "cover.close_cover",
    "run": "script.turn_on",
}


async def _store(hass: HomeAssistant):
    from homeassistant.helpers.storage import Store

    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


async def async_load(hass: HomeAssistant) -> dict[str, Any]:
    try:
        data = await (await _store(hass)).async_load()
    except Exception:  # noqa: BLE001
        return {"scenes": {}, "seen": []}
    if not isinstance(data, dict):
        return {"scenes": {}, "seen": []}
    data.setdefault("scenes", {})
    data.setdefault("seen", [])
    return data


async def async_save(hass: HomeAssistant, data: dict[str, Any]) -> None:
    try:
        await (await _store(hass)).async_save(data)
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("保存事件映射失败: %s", err)


async def async_set_mapping(
    hass: HomeAssistant,
    sid: str,
    *,
    entity_id: str | None = None,
    action: str = "toggle",
    name: str | None = None,
) -> dict[str, Any]:
    """登记/更新一条 场景 sid → HA 动作 映射；entity_id 为空表示删除。"""
    data = await async_load(hass)
    if not sid:
        return {"ok": False, "error": "缺少 sid"}
    if not entity_id:
        data["scenes"].pop(sid, None)
        await async_save(hass, data)
        return {"ok": True, "action": "delete", "sid": sid}
    if action not in ACTION_SERVICES:
        return {"ok": False, "error": f"未知动作: {action}"}
    data["scenes"][sid] = {
        "entity_id": entity_id,
        "action": action,
        "name": name or data["scenes"].get(sid, {}).get("name") or "",
    }
    await async_save(hass, data)
    return {"ok": True, "action": "set", "sid": sid, "mapping": data["scenes"][sid]}


def _remember(data: dict[str, Any], payload: dict[str, Any]) -> bool:
    """把收到的事件记进 seen（方便前端下拉选择），返回是否有变化。"""
    sid = payload.get("sid") or ""
    if not sid:
        return False
    seen = data["seen"]
    for item in seen:
        if item.get("sid") == sid:
            item["name"] = payload.get("name") or item.get("name")
            item["kind"] = payload.get("kind") or item.get("kind")
            item["last_status"] = payload.get("status") or item.get("last_status")
            return False
    seen.append({
        "sid": sid,
        "name": payload.get("name") or "",
        "kind": payload.get("kind") or "",
        "last_status": payload.get("status") or "",
    })
    return True


async def async_apply_event(hass: HomeAssistant, payload: dict[str, Any]) -> dict[str, Any]:
    """收到一条 HDL 事件：先记录，再按 sid 查映射并执行。"""
    data = await async_load(hass)
    changed = _remember(data, payload)
    if changed:
        await async_save(hass, data)

    sid = payload.get("sid") or ""
    rule = data["scenes"].get(sid)
    if not rule:
        LOGGER.debug("事件 %s（%s）没有映射规则", sid, payload.get("name"))
        return {"matched": False, "sid": sid}

    entity_id = rule.get("entity_id") or ""
    action = rule.get("action") or "toggle"
    service = ACTION_SERVICES.get(action)
    if not entity_id or not service:
        return {"matched": False, "sid": sid, "reason": "规则不完整"}

    try:
        await hass.services.async_call(
            *service.split("."), {"entity_id": entity_id}, blocking=False
        )
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("执行事件动作失败 %s → %s: %s", sid, entity_id, err)
        return {"matched": True, "sid": sid, "error": str(err)}

    LOGGER.info("HDL 事件 %s(%s) → %s(%s)", sid, payload.get("name"), entity_id, action)
    return {"matched": True, "sid": sid, "entity_id": entity_id, "action": action}


async def async_apply_down(hass: HomeAssistant, objects: list[dict[str, Any]]) -> dict[str, Any]:
    """若将来驱动把真实 down 送到集成，用同一套 service_calls 规则执行。"""
    data = await async_load(hass)
    executed = []
    for obj in objects:
        sid = str(obj.get("sid") or "")
        rule = data["scenes"].get(sid)
        if not rule:
            continue
        calls = service_calls(
            {"entity_id": rule.get("entity_id"), "spk": rule.get("spk")},
            obj.get("status") or [],
        )
        for call in calls:
            try:
                await hass.services.async_call(
                    *call["service"].split("."), call["data"], blocking=False
                )
                executed.append({"sid": sid, **call})
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("下发失败 %s: %s", sid, err)
    return {"executed": executed}
