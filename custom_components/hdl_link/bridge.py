"""HA 设备 → HDL 物模型（spk）注册表与报文构造。

设计依据（全部为 2026-11-19/20 在 MGWIP.430 网关 192.168.1.151:8586 上的实测）：

1. `device/add` / `function/attribute/add` 在本地 TCP 上确实落库（code=0 + 回读确认）；
2. 网关不认 `src` 作为在线依据：function 的 src 取 LINK / buspro / ha / 不填，
   回读 `online` 恒为 false；
3. 让功能点真正上线的手段是**主动上报属性**：
   `/base/{gw}/thing/property/up` + `objects:[{sid,status:[{key,value}]}]`
   之后回读 online=true 且 status 有值，`property/down` 才返回 code=0；
4. 但 `property/down` 不会把下行帧推给任何 LINK 客户端（已用双连接验证），
   状态也不会被网关改写 —— 即网关没有"把控制转给第三方客户端"的路由。
   因此桥接是**状态驱动**：HA → HDL（up 上报）可行；HDL → HA 需要网关侧驱动。

本模块只负责"注册报文 + 状态报文"的构造，不碰网络。
"""

from __future__ import annotations

import hashlib
from typing import Any

# ---------------------------------------------------------------- 标识
# 网关没有 manufacturer 字段，HA 来源靠 device_name + device_model 承载
DEVICE_NAME = "HA"
MODEL_PREFIX = "HA."
HW_VERSION = "HA-BRIDGE"

# ---------------------------------------------------------------- 五类物模型
# key: spk 名称；value: {"label","platform","attrs":[{key,data_type,value/min/max,rw}]}
# data_type 用网关实测值：string / integer / float / boolean
_ON_OFF = {"key": "on_off", "data_type": "string", "value": ["on", "off"], "rw": "rw"}
_ON_OFF_3 = {"key": "on_off", "data_type": "string", "value": ["on", "off", "stop"], "rw": "rw"}

SPK_PROFILES: dict[str, dict[str, Any]] = {
    # ---------------- 面板 / 电工
    "panel.socket": {
        "label": "面板-插座",
        "platform": "panel",
        "attrs": [
            dict(_ON_OFF),
            {"key": "delay", "data_type": "integer", "min": "0", "max": "3600", "rw": "rw"},
            {"key": "active_power", "data_type": "float", "rw": "r"},
            {"key": "voltage", "data_type": "float", "rw": "r"},
            {"key": "current", "data_type": "float", "rw": "r"},
            {"key": "total_electricity", "data_type": "float", "rw": "r"},
        ],
    },
    "panel.button": {
        "label": "面板-按键",
        "platform": "panel",
        "attrs": [
            {"key": "lock", "data_type": "string", "value": ["lock", "unlock"], "rw": "rw"},
            {"key": "on_off", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
            # 只读：按键事件，HDL → HA 侧唯一的"输入"来源
            {"key": "key_event", "data_type": "string", "value": ["down", "up"], "rw": "r"},
        ],
    },
    "panel.common": {
        "label": "面板-公共",
        "platform": "panel",
        "attrs": [
            {"key": "lock", "data_type": "string", "value": ["lock", "unlock"], "rw": "rw"},
            {"key": "back_light", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
            {"key": "sleep", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
        ],
    },
    "light.switch": {
        "label": "电工-开关灯",
        "platform": "light",
        "attrs": [
            dict(_ON_OFF),
            {"key": "delay", "data_type": "integer", "min": "0", "max": "3600", "rw": "rw"},
            {"key": "current", "data_type": "float", "rw": "r"},
            {"key": "err_msg", "data_type": "string", "rw": "r"},
        ],
    },
    "light.dimming": {
        "label": "电工-调光灯",
        "platform": "light",
        "attrs": [
            dict(_ON_OFF),
            {"key": "brightness", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
            {"key": "fade_time", "data_type": "integer", "min": "0", "max": "3600", "rw": "rw"},
            {"key": "relative_brightness", "data_type": "integer", "min": "-100", "max": "100", "rw": "w"},
            {"key": "power", "data_type": "float", "rw": "r"},
            {"key": "current", "data_type": "float", "rw": "r"},
        ],
    },
    "light.cct": {
        "label": "电工-色温灯",
        "platform": "light",
        "attrs": [
            dict(_ON_OFF),
            {"key": "brightness", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
            {"key": "cct", "data_type": "integer", "min": "0", "max": "65535", "rw": "rw"},
            {"key": "fade_time", "data_type": "integer", "min": "0", "max": "3600", "rw": "rw"},
            {"key": "power", "data_type": "float", "rw": "r"},
        ],
    },
    "light.rgb": {
        "label": "电工-RGB 灯",
        "platform": "light",
        "attrs": [
            dict(_ON_OFF),
            {"key": "brightness", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
            {"key": "rgb", "data_type": "string", "rw": "rw"},
            {"key": "mode", "data_type": "string", "value": ["cct", "rgbw"], "rw": "rw"},
        ],
    },
    "light.rgbw": {
        "label": "电工-RGBW 灯",
        "platform": "light",
        "attrs": [
            dict(_ON_OFF),
            {"key": "brightness", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
            {"key": "rgbw", "data_type": "string", "rw": "rw"},
            {"key": "mode", "data_type": "string", "value": ["cct", "rgbw"], "rw": "rw"},
        ],
    },
    "other.common": {
        "label": "通用开关",
        "platform": "other",
        "attrs": [dict(_ON_OFF)],
    },
    # ---------------- 窗帘
    "curtain.switch": {
        "label": "窗帘-开关型",
        "platform": "curtain",
        "attrs": [dict(_ON_OFF_3)],
    },
    "curtain.roller": {
        "label": "窗帘-卷帘",
        "platform": "curtain",
        "attrs": [
            dict(_ON_OFF_3),
            {"key": "percent", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
        ],
    },
    "curtain.trietex": {
        "label": "窗帘-百叶",
        "platform": "curtain",
        "attrs": [
            dict(_ON_OFF_3),
            {"key": "percent", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
            {"key": "angle", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
        ],
    },
    "curtain.shades": {
        "label": "窗帘-遮阳",
        "platform": "curtain",
        "attrs": [
            dict(_ON_OFF_3),
            {"key": "percent", "data_type": "integer", "min": "0", "max": "100", "rw": "rw"},
        ],
    },
    # ---------------- 传感器（只读：由 HA 周期上报）
    "sensor.temperature": {
        "label": "传感器-温度",
        "platform": "sensor",
        "attrs": [
            {"key": "temperature", "data_type": "float", "min": "-40", "max": "80", "rw": "r"},
            {"key": "temperature_type", "data_type": "string", "value": ["℃", "℉"], "rw": "rw"},
            {"key": "enable", "data_type": "boolean", "rw": "rw"},
            {"key": "battery_percentage", "data_type": "integer", "min": "0", "max": "100", "rw": "r"},
        ],
    },
    "sensor.humidity": {
        "label": "传感器-湿度",
        "platform": "sensor",
        "attrs": [
            {"key": "humidity", "data_type": "float", "min": "0", "max": "100", "rw": "r"},
            {"key": "enable", "data_type": "boolean", "rw": "rw"},
            {"key": "battery_percentage", "data_type": "integer", "min": "0", "max": "100", "rw": "r"},
        ],
    },
    "sensor.light": {
        "label": "传感器-光照",
        "platform": "sensor",
        "attrs": [
            {"key": "illuminance", "data_type": "float", "min": "0", "max": "10000", "rw": "r"},
            {"key": "enable", "data_type": "boolean", "rw": "rw"},
        ],
    },
    "sensor.pir": {
        "label": "传感器-人体感应",
        "platform": "sensor",
        "attrs": [
            {"key": "people_status", "data_type": "string", "value": ["detected", "undetected"], "rw": "r"},
            {"key": "sensitivity", "data_type": "string", "value": ["high", "middle", "low"], "rw": "rw"},
            {"key": "enable", "data_type": "boolean", "rw": "rw"},
        ],
    },
    "sensor.doorwindow": {
        "label": "传感器-门磁",
        "platform": "sensor",
        "attrs": [
            {"key": "door_status", "data_type": "string", "value": ["open", "close"], "rw": "r"},
            {"key": "enable", "data_type": "boolean", "rw": "rw"},
        ],
    },
    "sensor.co2": {
        "label": "传感器-CO2",
        "platform": "sensor",
        "attrs": [{"key": "co2", "data_type": "float", "min": "0", "max": "5000", "rw": "r"}],
    },
    "sensor.pm25": {
        "label": "传感器-PM2.5",
        "platform": "sensor",
        "attrs": [{"key": "pm25", "data_type": "float", "min": "0", "max": "1000", "rw": "r"}],
    },
    # ---------------- 暖通
    "hvac.ac": {
        "label": "暖通-空调",
        "platform": "hvac",
        "attrs": [
            dict(_ON_OFF),
            {"key": "mode", "data_type": "string",
             "value": ["cool", "heat", "fan", "auto", "dry", "sleep"], "rw": "rw"},
            {"key": "fan", "data_type": "string",
             "value": ["high", "medium", "low", "auto"], "rw": "rw"},
            {"key": "set_temp", "data_type": "float", "min": "0", "max": "30", "rw": "rw"},
            {"key": "swing", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
            {"key": "room_temp", "data_type": "float", "rw": "r"},
            {"key": "room_humidity", "data_type": "float", "rw": "r"},
        ],
    },
    "hvac.fan": {
        "label": "暖通-风机",
        "platform": "hvac",
        "attrs": [
            dict(_ON_OFF),
            {"key": "fan_speed_percent", "data_type": "integer", "min": "1", "max": "8", "rw": "rw"},
        ],
    },
    "hvac.airFresh": {
        "label": "暖通-新风",
        "platform": "hvac",
        "attrs": [
            dict(_ON_OFF),
            {"key": "mode", "data_type": "string",
             "value": ["auto", "manual", "timer", "smart"], "rw": "rw"},
            {"key": "co2", "data_type": "float", "rw": "r"},
            {"key": "pm25", "data_type": "float", "rw": "r"},
            {"key": "humidity", "data_type": "float", "rw": "r"},
        ],
    },
    "hvac.cac": {
        "label": "暖通-中央空调",
        "platform": "hvac",
        "attrs": [
            dict(_ON_OFF),
            {"key": "cac_valve", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
            {"key": "fh_valve", "data_type": "string", "value": ["on", "off"], "rw": "rw"},
            {"key": "set_temp", "data_type": "float", "min": "0", "max": "30", "rw": "rw"},
            {"key": "room_temp", "data_type": "float", "rw": "r"},
            {"key": "alarm", "data_type": "string", "rw": "r"},
        ],
    },
}

# ---------------------------------------------------------------- HA 实体 → spk
_DOMAIN_SPK: dict[str, str] = {
    "light": "light.switch",
    "switch": "other.common",
    "cover": "curtain.roller",
    "climate": "hvac.ac",
    "fan": "hvac.fan",
    "humidifier": "hvac.airFresh",
    "media_player": "other.common",
    "lock": "other.common",
    "sensor": "sensor.temperature",
    "binary_sensor": "sensor.doorwindow",
}

# sensor 的 device_class → spk
_SENSOR_CLASS_SPK: dict[str, str] = {
    "temperature": "sensor.temperature",
    "humidity": "sensor.humidity",
    "illuminance": "sensor.light",
    "motion": "sensor.pir",
    "occupancy": "sensor.pir",
    "door": "sensor.doorwindow",
    "window": "sensor.doorwindow",
    "opening": "sensor.doorwindow",
    "carbon_dioxide": "sensor.co2",
    "pm25": "sensor.pm25",
}


def spk_for_entity(domain: str, device_class: str | None = None,
                   supported_features: int = 0) -> str:
    """按 HA 域/device_class 选一个 HDL spk。

    light/cover 会按 supported_features 升精度：支持调光/色温/颜色 → 对应 spk。
    """
    if domain == "sensor":
        return _SENSOR_CLASS_SPK.get((device_class or "").lower(), "sensor.temperature")
    if domain == "binary_sensor":
        return _SENSOR_CLASS_SPK.get((device_class or "").lower(), "sensor.doorwindow")
    if domain == "light":
        feat = supported_features or 0
        # HA: COLOR_MODE_RGB=1? 这里用宽松判定：>=64 视为支持颜色/色温
        if feat >= 64:
            return "light.rgbw"
        if 16 <= feat < 64:
            return "light.cct"
        if 8 <= feat < 16:
            return "light.dimming"
        return "light.switch"
    if domain == "cover":
        return "curtain.roller"
    if domain == "switch" and (device_class or "") == "outlet":
        return "panel.socket"
    return _DOMAIN_SPK.get(domain, "other.common")


# ---------------------------------------------------------------- oid / sid 生成
def device_oid(device_id: str) -> str:
    """由 HA device_id/entity 稳定派生 16 位十六进制 oid（0A 前缀便于识别）。"""
    digest = hashlib.sha1(str(device_id).encode("utf-8")).hexdigest().upper()
    return "0A" + digest[:14]


def device_mac(device_id: str) -> str:
    return hashlib.sha1(str(device_id).encode("utf-8")).hexdigest().upper()[:12]


def function_sid(oid: str, seq: int) -> str:
    """sid = oid(16) + 4 位序号段 + 4 位尾段，共 28 位（网关实测口径）。"""
    return f"{oid}0201{seq:04d}0101"


# ---------------------------------------------------------------- 报文构造
def _clean_attr(attr: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"key": attr["key"], "data_type": attr["data_type"]}
    if "value" in attr and attr["value"] is not None:
        out["value"] = list(attr["value"])
    if "min" in attr:
        out["min"] = str(attr["min"])
    if "max" in attr:
        out["max"] = str(attr["max"])
    return out


def function_attributes(spk: str) -> list[dict[str, Any]]:
    profile = SPK_PROFILES.get(spk)
    return [_clean_attr(a) for a in (profile or {}).get("attrs", [])]


def device_payload(oid: str, domain: str, device_id: str, name: str) -> dict[str, Any]:
    """物理模型 objects 元素：HA 标识写在 device_name / device_model。"""
    return {
        "oid": oid,
        "status": "online",
        "device_name": DEVICE_NAME,
        "device_model": f"{MODEL_PREFIX}{domain or 'device'}"[:32],
        "device_mac": device_mac(device_id),
        "hw_version": HW_VERSION,
        "fw_version": "2.4.0",
        "addresses": "0E01",
        "driver_code": "",
    }


def function_payload(oid: str, sid: str, spk: str, name: str, domain: str) -> dict[str, Any]:
    return {
        "objects": [{
            "oid": oid,
            "sid": sid,
            "name": str(name)[:32],
            "spk": spk,
            "omodel": f"{MODEL_PREFIX}{domain or 'device'}"[:32],
            "attributes": function_attributes(spk),
        }]
    }


def up_body(sid: str, status: list[dict[str, Any]]) -> dict[str, Any]:
    """让功能点上线 + 同步状态：网关只认 property/up。"""
    return {"objects": [{"sid": sid, "status": status}]}


# ---------------------------------------------------------------- 值换算
_BOOL_STR = {True: "on", False: "off"}


def to_hdl_value(spk: str, key: str, value: Any) -> str | None:
    """HA 状态值 → HDL 属性值（字符串口径，网关 status 里就是字符串）。"""
    if value is None or value in ("unknown", "unavailable"):
        return None
    if key == "on_off":
        if spk.startswith("curtain"):
            return {"open": "on", "closed": "off", "opening": "on", "closing": "on"}.get(str(value), str(value))
        if isinstance(value, bool):
            return _BOOL_STR[value]
        return str(value)
    if key == "brightness":
        try:
            return str(int(round(float(value) / 255 * 100)))
        except (TypeError, ValueError):
            return None
    if key == "cct":
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return None
    if key == "percent":
        # HA cover 的 current_position 与 HDL percent 同向（0=关 100=开）
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return None
    if key in ("temperature", "humidity", "illuminance", "co2", "pm25", "room_temp",
               "room_humidity", "set_temp"):
        try:
            return "%.2f" % float(value)
        except (TypeError, ValueError):
            return None
    if key == "people_status":
        return "detected" if str(value) in ("on", "detected", "True", "true") else "undetected"
    if key == "door_status":
        return "open" if str(value) in ("on", "open", "True", "true") else "close"
    return str(value)
