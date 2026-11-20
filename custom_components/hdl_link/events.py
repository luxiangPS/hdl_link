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
import time
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

# 事件统计的落盘节流：最多每 SAVE_INTERVAL 秒写一次（连按/状态暴雨时不刷爆存储）
SAVE_INTERVAL = 3
_last_save_ts = 0.0

# 进程内事件台账：面板「最近事件」优先读这里，不依赖存储落盘是否成功
# （落盘只是为了让重启后还能看到历史；实时自检只看内存，毫秒级生效）
_MEM_SEEN: dict[str, dict[str, Any]] = {}


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
    # 旧版本（≤2.8.9）的 seen 只有 sid/kind，没有事件时间与次数，面板会显示「未收到过」。
    # 这类空壳记录直接丢掉，下一次事件会以新结构重新写入。
    if isinstance(data.get("seen"), list):
        data["seen"] = [
            item
            for item in data["seen"]
            if isinstance(item, dict) and item.get("last_at") and item.get("count")
        ]
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


def _maybe_dirty(now: int) -> bool:
    """节流：同一秒内的连续事件合并，最多每 SAVE_INTERVAL 秒落一次盘。"""
    global _last_save_ts
    if now - _last_save_ts >= SAVE_INTERVAL:
        _last_save_ts = now
        return True
    return False


def _remember(data: dict[str, Any], sid: str, values: dict[str, str], kind: str) -> bool:
    """把收到过的功能点记进 seen（前端下拉挑 sid 用），返回是否需要落盘。

    同一条会累计 ``count`` 并更新 ``last_at``（Unix 秒），面板上用来判断
    「面板按键到底有没有把事件送到 HA」——这是场景桥/按键桥现场自检的核心依据。
    """
    global _last_save_ts
    seen = data["seen"]
    snapshot = ";".join(f"{k}={v}" for k, v in sorted(values.items()))
    now = int(time.time())
    for item in seen:
        if item.get("sid") == sid:
            item["last_status"] = snapshot or item.get("last_status") or ""
            item["kind"] = kind or item.get("kind") or ""
            item["last_at"] = now
            item["count"] = int(item.get("count") or 0) + 1
            _MEM_SEEN[sid] = dict(item)
            return _maybe_dirty(now)
    seen.append(
        {
            "sid": sid,
            "name": "",
            "kind": kind,
            "last_status": snapshot,
            "last_at": now,
            "count": 1,
        }
    )
    _MEM_SEEN[sid] = dict(seen[-1])
    if len(seen) > 400:
        # 只保留最近 400 个 sid，防止长期运行把存储撑大（面板只需近期的自检信息）
        seen.sort(key=lambda item: int(item.get("last_at") or 0), reverse=True)
        del seen[400:]
    _last_save_ts = now
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
    LOGGER.warning("HDL 上行批次 kind=%s 条数=%d", kind, len(objects))
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
        "seen": _merge_seen(data.get("seen") or [], _MEM_SEEN),
        "modes": list(MODES),
        "actions": list(ACTION_SERVICES),
        "triggers": list(TRIGGERS),
    }


def _merge_seen(saved: list[dict[str, Any]], live: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """存储里的历史台账 + 进程内实时台账，同 sid 取次数大的那份。"""
    merged: dict[str, dict[str, Any]] = {}
    for item in saved:
        sid = str((item or {}).get("sid") or "")
        if sid:
            merged[sid] = dict(item)
    for sid, item in (live or {}).items():
        old = merged.get(sid) or {}
        merged[sid] = item if int(item.get("count") or 0) >= int(old.get("count") or 0) else old
    return list(merged.values())


def join_scene_bindings(
    scenes: list[dict[str, Any]],
    binds: dict[str, Any],
    seen: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把网关场景清单和本地绑定表拼成一张排查表（纯函数，便于自测）。

    输出三件事：
    - scenes：每个场景 + 是否已绑定、绑到哪个实体、什么模式/动作；
    - unbound：网关有、但 HA 完全没绑的场景名（面板按了不可能有反应）；
    - orphan_bindings：HA 绑了、但网关里已经没有的 sid（绑了但永远不触发）。
    """
    items = [_bridge_row(scene, binds, seen) for scene in scenes or []]
    gateway_sids = {item["sid"] for item in items}
    orphan = [
        {"sid": sid, "entity_id": (rule or {}).get("entity_id")}
        for sid, rule in (binds or {}).items()
        if sid not in gateway_sids
    ]
    return {
        "count": len(items),
        "bound": sum(1 for item in items if item["bound"]),
        "unbound": [item["name"] for item in items if not item["bound"]],
        "scenes": items,
        "orphan_bindings": orphan,
    }


def _event_stats(sid: str, seen: list[dict[str, Any]] | None) -> dict[str, Any]:
    for item in seen or []:
        if item.get("sid") == sid:
            return {
                "last_at": item.get("last_at"),
                "count": int(item.get("count") or 0),
                "last_status": item.get("last_status") or "",
                "kind": item.get("kind") or "",
            }
    return {"last_at": None, "count": 0, "last_status": "", "kind": ""}


def _bridge_row(
    src: dict[str, Any],
    binds: dict[str, Any],
    seen: list[dict[str, Any]] | None,
    source: str = "scene",
) -> dict[str, Any]:
    sid = str(src.get("sid") or "")
    rule = (binds or {}).get(sid) or {}
    stats = _event_stats(sid, seen)
    return {
        "sid": sid,
        "name": src.get("name"),
        "status": src.get("status"),
        "source": source,
        "oid": src.get("oid"),
        "omodel": src.get("omodel"),
        "spk": src.get("spk"),
        "online": src.get("online", True),
        "bound": bool(rule),
        "entity_id": rule.get("entity_id"),
        "mode": rule.get("mode"),
        "action": rule.get("action"),
        "trigger": rule.get("trigger"),
        "last_at": stats["last_at"],
        "count": stats["count"],
        "last_status": stats["last_status"],
    }


def join_bridge_rows(
    scenes: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    binds: dict[str, Any],
    seen: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """「按键桥」面板的数据源：场景 + 可绑定的真实功能点 + 绑定状态 + 事件统计。

    ``functions`` 里每条是网关 ``function/list/get`` 的对象（sid/name/oid/omodel/spk）。
    纯函数：不碰网关、不碰存储，方便单测和前端反复刷新。
    """
    rows = [_bridge_row(scene, binds, seen, "scene") for scene in scenes or []]
    rows += [_bridge_row(fn, binds, seen, "function") for fn in functions or []]
    gateway_sids = {row["sid"] for row in rows}
    orphan = [
        {"sid": sid, "entity_id": (rule or {}).get("entity_id")}
        for sid, rule in (binds or {}).items()
        if sid not in gateway_sids
    ]
    bound_rows = [row for row in rows if row["bound"]]
    fired_rows = [row for row in rows if row["count"]]
    return {
        "rows": rows,
        "counts": {
            "total": len(rows),
            "scenes": sum(1 for row in rows if row["source"] == "scene"),
            "functions": sum(1 for row in rows if row["source"] == "function"),
            "bound": len(bound_rows),
            "unbound": len(rows) - len(bound_rows),
            "fired": len(fired_rows),
        },
        "unbound": [row["name"] for row in rows if not row["bound"]],
        "orphan_bindings": orphan,
    }
