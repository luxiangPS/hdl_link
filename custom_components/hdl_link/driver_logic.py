"""下行 status → HA 服务调用的纯逻辑（不依赖 Home Assistant，便于单测）。"""

from __future__ import annotations

from typing import Any


def normalize_status(status: Any) -> list[dict[str, str]]:
    """把各种形态的 status 归一成 [{"key","value"}]（list / dict / None 都能吃）。"""
    out: list[dict[str, str]] = []
    if isinstance(status, list):
        for item in status:
            if isinstance(item, dict) and item.get("key") is not None:
                out.append({"key": str(item["key"]), "value": str(item.get("value"))})
    elif isinstance(status, dict):
        for key, value in status.items():
            out.append({"key": str(key), "value": str(value)})
    return out


EVENT_KINDS = ("scene", "logic")


def is_event_kind(kind: str) -> bool:
    """场景/逻辑执行事件：每次都是一次动作，不是状态，需要绕过状态去重。"""
    return str(kind) in EVENT_KINDS


def event_values(obj: dict[str, Any]) -> dict[str, str]:
    """场景/逻辑事件的字段：{"sid":…, "status":"on"/"success", "enable":"true"}。

    这类事件的 status 是一个字符串（不是 status 数组），单独取出来当触发值。
    """
    out: dict[str, str] = {}
    raw = obj.get("status")
    if isinstance(raw, str) and raw:
        out["status"] = raw
    if obj.get("enable") is not None:
        out["enable"] = str(obj.get("enable"))
    return out


def should_fire(trigger: str, values: dict[str, str], status: list[dict[str, str]]) -> bool:
    """功能点上报是否值得下发：trigger=any|on|off，空 status 不下发。"""
    if trigger in ("on", "off") and values.get("on_off") != trigger:
        return False
    return bool(status)


def service_calls(info: dict[str, Any], status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 HDL 的一条 status 数组翻译成 HA 服务调用列表。

    info: {"entity_id","spk"}
    status: [{"key","value"}]
    返回: [{"service": "light.turn_on", "data": {...}}]
    """
    entity_id = info.get("entity_id") or ""
    spk = info.get("spk") or ""
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    values = {str(x.get("key")): str(x.get("value")) for x in status if x.get("key")}
    calls: list[dict[str, Any]] = []

    def add(service: str, **data: Any) -> None:
        calls.append({"service": service, "data": {"entity_id": entity_id, **data}})

    if not entity_id:
        return calls

    on_off = values.get("on_off")
    if on_off is not None:
        if spk.startswith("curtain") or domain == "cover":
            if on_off == "stop":
                add("cover.stop_cover")
            elif on_off == "on":
                add("cover.open_cover")
            elif on_off == "off":
                add("cover.close_cover")
        elif domain == "climate":
            add("climate.turn_on" if on_off == "on" else "climate.turn_off")
        elif domain == "lock":
            # HDL 的 on 表示「已锁」，off 表示「开锁」
            add("lock.lock" if on_off == "on" else "lock.unlock")
        elif domain == "valve":
            add("valve.open_valve" if on_off == "on" else "valve.close_valve")
        elif domain == "vacuum":
            add("vacuum.start" if on_off == "on" else "vacuum.return_to_base")
        elif on_off == "on":
            add(f"{domain}.turn_on")
        elif on_off == "off":
            add(f"{domain}.turn_off")

    if "brightness" in values and domain == "light":
        try:
            pct = max(0, min(100, int(float(values["brightness"]))))
        except ValueError:
            pct = None
        if pct is not None:
            add("light.turn_on", brightness_pct=pct)

    if "cct" in values and domain == "light":
        try:
            kelvin = int(float(values["cct"]))
        except ValueError:
            kelvin = 0
        if kelvin > 0:
            add("light.turn_on", color_temp_kelvin=kelvin)

    if "rgb" in values and domain == "light":
        parts = [p.strip() for p in values["rgb"].split(",")]
        if len(parts) == 3:
            try:
                r, g, b = (int(p) for p in parts)
                add("light.turn_on", rgb_color=[r, g, b])
            except ValueError:
                pass

    if "percent" in values and domain == "cover":
        try:
            add("cover.set_cover_position", position=int(float(values["percent"])))
        except ValueError:
            pass

    if "set_temp" in values and domain == "climate":
        try:
            add("climate.set_temperature", temperature=float(values["set_temp"]))
        except ValueError:
            pass

    if "mode" in values and domain == "climate":
        mode = values["mode"]
        if mode in ("cool", "heat", "fan", "dry", "auto"):
            add("climate.set_hvac_mode", hvac_mode=mode)

    if "fan" in values and domain == "climate":
        add("climate.set_fan_mode", fan_mode=values["fan"])

    # 摆动（swing）：HA 的 swing_mode 取值各家不同，不在这里猜，避免下发非法参数

    # 暖通-风机：HDL 1–8 档 → HA 百分比
    if "fan_speed_percent" in values and domain in ("fan", "climate"):
        try:
            step = int(float(values["fan_speed_percent"]))
        except ValueError:
            step = 0
        if step > 0:
            pct = max(1, min(100, round(step / 8 * 100)))
            if domain == "fan":
                add("fan.turn_on", percentage=pct)
            else:
                add("climate.turn_on")

    if "percentage" in values and domain == "fan":
        try:
            pct = max(0, min(100, int(float(values["percentage"]))))
        except ValueError:
            pct = None
        if pct is not None:
            add("fan.turn_on", percentage=pct)

    if "angle" in values and domain == "cover":
        try:
            tilt = max(0, min(100, int(float(values["angle"]))))
        except ValueError:
            tilt = None
        if tilt is not None:
            add("cover.set_cover_tilt_position", tilt_position=tilt)

    return calls
