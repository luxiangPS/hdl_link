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

from .driver_logic import (
    event_values,
    is_event_kind,
    normalize_status,
    service_calls,
    should_fire,
)

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
        is_event = is_event_kind(kind)
        status = normalize_status(obj.get("status"))
        values = {item["key"]: item["value"] for item in status}
        if is_event:
            # 场景/逻辑事件的 status 是字符串（"on" / "success"），不是属性数组
            values = {**values, **event_values(obj)}
        if kind != "down" and not is_event and sid in own:
            # 上行属性：我们自己注册进网关的 HA 设备由协调器刷新，不参与绑定判断
            continue
        if _remember(data, sid, values, kind):
            dirty = True
        if not status and not is_event:
            continue

        rule = data["sids"].get(sid)
        auto = None
        if rule is None and kind in ("down", "function", "up"):
            # 网关把面板/App 对 HA 虚拟设备的控制当成属性变更，以 property/up 广播回来
            #（实测：按一次「虚拟可调灯」就收到一条 up，sids=该虚拟 sid，没有 down）
            # 这类 sid 直接用 provision 映射自动执行
            from .provision import async_entity_for_sid

            auto = await async_entity_for_sid(hass, sid)
            if auto:
                rule = {"entity_id": auto.get("entity_id"), "spk": auto.get("spk"),
                        "mode": "follow", "trigger": "any"}
        if not rule:
            control_like = {"on_off", "brightness", "percent", "position", "cct", "mode", "set_temp"}
            if is_event:
                LOGGER.warning("HDL 场景/逻辑事件未绑定: kind=%s sid=%s，"
                               "可用服务 hdl_link.bind（或 /api/hdl_link/binding）绑定，mode=action", kind, sid)
            elif kind != "down" and (set(values) & control_like):
                # 控制类属性没匹配到任何映射才报警；传感器周期上报不刷日志
                LOGGER.warning("HDL 控制类上报未命中任何映射: sid=%s status=%s（kind=%s）",
                               sid, values, kind)
            continue

        if is_event:
            # 场景/逻辑事件每次都是一次「按下」，必须每次执行，不参与状态去重
            if rule.get("mode") != "action":
                LOGGER.warning("场景 %s 绑定的是 follow 模式，事件没有属性状态可翻译；"
                               "请改成 mode=action（toggle/turn_on/…）", sid)
                continue
            if values.get("status") not in ("on", "success", "true"):
                continue
        else:
            key = ";".join(sorted(f"{k}={v}" for k, v in values.items()))
            if kind != "down" and data["state"].get(sid) == key:
                # 同一份 status 已经下发过，重复上报（含自己发 up 的回环）不再执行
                if auto:
                    LOGGER.warning("HDL 控制被去重跳过: sid=%s status=%s（与上次相同）", sid, values)
                continue
            data["state"][sid] = key
            if not should_fire(rule.get("trigger") or "any", values, status):
                if auto:
                    LOGGER.warning("HDL 控制未满足触发条件 %s: sid=%s status=%s",
                                   rule.get("trigger"), sid, values)
                continue

        entity_id = rule.get("entity_id") or ""
        if not entity_id:
            continue

        if rule.get("mode") == "action":
            service = ACTION_SERVICES.get(rule.get("action") or "toggle")
            calls = [{"service": service, "data": {"entity_id": entity_id}}] if service else []
        else:
            calls = service_calls({"entity_id": entity_id, "spk": rule.get("spk") or ""}, status)

        LOGGER.warning("HDL 控制执行: sid=%s 实体=%s 调用=%d 状态=%s",
                       sid, entity_id, len(calls), values)
        if not calls:
            LOGGER.warning("HDL 控制翻译成 0 个服务调用（spk=%s status=%s）",
                           rule.get("spk"), status)
        for call in calls:
            try:
                await hass.services.async_call(
                    *call["service"].split("."), call["data"], blocking=False
                )
                executed.append({"sid": sid, **call})
                LOGGER.warning("HDL 控制已下发 %s → %s %s",
                               sid, call["service"], call.get("data"))
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("下发失败 %s → %s: %s", sid, call["service"], err)

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
