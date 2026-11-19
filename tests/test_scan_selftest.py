"""scan.py 自测：用假 HA 注册表跑真实 async_scan，覆盖 4 类场景。

运行：python test_scan_selftest.py
"""
from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

BASE = r"C:\Users\luxiang\Doubao\chats\2026-11-16\new-chat-1\hdl_link-integration\custom_components\hdl_link"


# ----------------------------------------------------------------- 假 HA 模块
def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class _State:
    def __init__(self, entity_id: str, state: str, attributes: dict[str, Any]) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes


class _States:
    def __init__(self, states: dict[str, _State]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _State | None:
        return self._states.get(entity_id)


class _Entry:
    def __init__(self, entity_id: str, device_id: str, original_name: str | None = None,
                 device_class: str | None = None, disabled: bool = False) -> None:
        self.entity_id = entity_id
        self.device_id = device_id
        self.original_name = original_name or entity_id
        self.original_device_class = device_class
        self.device_class = device_class
        self.disabled_by = "user" if disabled else None


class _EntReg:
    def __init__(self, entries: dict[str, list[_Entry]]) -> None:
        self._entries = entries

    def async_entries_for_device(self, device_id: str, include_disabled_entities: bool = False):
        return list(self._entries.get(device_id, []))


class _Device:
    def __init__(self, device_id: str, name: str, identifiers: set[tuple[str, str]],
                 entries: list[str] | None = None, area_id: str | None = None,
                 disabled: bool = False) -> None:
        self.id = device_id
        self.name = name
        self.name_by_user = None
        self.model = "TEST-MODEL"
        self.manufacturer = "TEST-MAKER"
        self.area_id = area_id
        self.disabled = disabled
        self.identifiers = identifiers
        self.config_entries = set(entries or [])
        self.via_device_id = None
        self.entry_type = None


class _DevReg:
    def __init__(self, devices: list[_Device]) -> None:
        self.devices = {d.id: d for d in devices}


class _Area:
    def __init__(self, name: str) -> None:
        self.name = name


class _AreaReg:
    def __init__(self, areas: dict[str, _Area]) -> None:
        self._areas = areas

    def async_get_area(self, area_id: str):
        return self._areas.get(area_id)


class _ConfigEntry:
    def __init__(self, domain: str, title: str) -> None:
        self.domain = domain
        self.title = title


class _ConfigEntries:
    def __init__(self, entries: dict[str, _ConfigEntry]) -> None:
        self._entries = entries

    def async_get_entry(self, entry_id: str):
        return self._entries.get(entry_id)


class _Hass:
    def __init__(self, states, dev_reg, ent_reg, area_reg, cfg) -> None:
        self.states = states
        self.data: dict[str, Any] = {}
        self.config_entries = cfg
        self._dev_reg = dev_reg
        self._ent_reg = ent_reg
        self._area_reg = area_reg


def install_stubs(hass_holder: dict[str, Any]) -> None:
    core = _module("homeassistant.core", HomeAssistant=object)
    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []  # 声明为包，便于挂子模块

    _module("homeassistant.helpers.device_registry",
            async_get=lambda hass: hass._dev_reg)
    _module("homeassistant.helpers.entity_registry",
            async_get=lambda hass: hass._ent_reg,
            RegistryEntry=_Entry)
    _module("homeassistant.helpers.area_registry",
            async_get=lambda hass: hass._area_reg)
    _module("homeassistant.const", PERCENTAGE="%", STATE_UNAVAILABLE="unavailable")
    setattr(helpers, "device_registry", sys.modules["homeassistant.helpers.device_registry"])
    setattr(helpers, "entity_registry", sys.modules["homeassistant.helpers.entity_registry"])
    setattr(helpers, "area_registry", sys.modules["homeassistant.helpers.area_registry"])
    _module("homeassistant", core=core, helpers=helpers, const=sys.modules["homeassistant.const"])


def build_hass() -> _Hass:
    # 设备 1：普通 MQTT 灯（可控）
    light_entries = [_Entry("light.kitchen", "dev_light"), _Entry("sensor.kitchen_temp", "dev_light",
                                                                 device_class="temperature")]
    # 设备 2：HDL LINK 自己（已在网关，只读）
    hdl_entries = [_Entry("light.channel_08", "dev_hdl"), _Entry("light.channel_08", "dev_hdl")]
    # 设备 3：系统集成 sun，无可写功能 → 应被跳过
    sun_entries = [_Entry("sensor.sun_next_dawn", "dev_sun")]
    # 设备 4：开关设备（重名演示）
    sw_entries = [_Entry("switch.a", "dev_sw"), _Entry("switch.b", "dev_sw")]

    entry_map = {
        "dev_light": light_entries,
        "dev_hdl": hdl_entries,
        "dev_sun": sun_entries,
        "dev_sw": sw_entries,
    }
    dev_reg = _DevReg([
        _Device("dev_light", "厨房灯", {("mqtt", "kitchen-1")}, ["cfg_mqtt"], "area_1"),
        _Device("dev_hdl", "HDL 功能模块", {("hdl_link", "010105003DF6F100")}, ["cfg_hdl"], "area_1"),
        _Device("dev_sun", "Sun", {("sun", "sun")}, ["cfg_sun"]),
        _Device("dev_sw", "双路开关", {("mqtt", "sw-1")}, ["cfg_mqtt"], "area_2"),
    ])

    states = _States({
        "light.kitchen": _State("light.kitchen", "on", {"friendly_name": "厨房灯", "supported_features": 0}),
        "sensor.kitchen_temp": _State("sensor.kitchen_temp", "23.5",
                                      {"friendly_name": "厨房温度", "unit_of_measurement": "°C",
                                       "device_class": "temperature"}),
        "light.channel_08": _State("light.channel_08", "on", {"friendly_name": "通道 08"}),
        "sensor.sun_next_dawn": _State("sensor.sun_next_dawn", "2026-11-20T22:10:00",
                                       {"friendly_name": "Next dawn"}),
        # 重名：两个 switch 都叫「通用开关」
        "switch.a": _State("switch.a", "on", {"friendly_name": "通用开关"}),
        "switch.b": _State("switch.b", "off", {"friendly_name": "通用开关"}),
    })
    cfg = _ConfigEntries({
        "cfg_mqtt": _ConfigEntry("mqtt", "MQTT"),
        "cfg_hdl": _ConfigEntry("hdl_link", "HDL LINK"),
        "cfg_sun": _ConfigEntry("sun", "Sun"),
    })
    area_reg = _AreaReg({"area_1": _Area("一楼"), "area_2": _Area("二楼")})
    return _Hass(states, dev_reg, _EntReg(entry_map), area_reg, cfg)


async def main() -> int:
    install_stubs({})
    sys.modules.pop("hdl_link", None)
    pkg = types.ModuleType("hdl_link")
    pkg.__path__ = [BASE]
    sys.modules["hdl_link"] = pkg

    import hdl_link.scan as scan  # noqa: PLC0415

    hass = build_hass()
    result = await scan.async_scan(hass)

    failures: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
        if not cond:
            failures.append(name)

    devices = result["devices"]
    names = [d["name"] for d in devices]
    print("\n扫描结果：", names)
    print("概览：", {k: result[k] for k in
                    ("device_count", "function_count", "supported_count",
                     "existing_device_count", "duplicate_groups")})

    check("普通设备被扫描到", "厨房灯" in names)
    check("HDL 已有设备被列出", "HDL 功能模块" in names)
    check("系统集成无可用功能被跳过", "Sun" not in names)
    check("HDL 已有设备排在第一位", names and names[0] == "HDL 功能模块", names[:1] and names[0])
    hdl = next((d for d in devices if d["name"] == "HDL 功能模块"), None)
    check("HDL 设备标记 existing", bool(hdl and hdl["existing"]))
    check("HDL 设备功能标为 supported", bool(hdl and all(f["supported"] for f in hdl["functions"])))
    light = next((d for d in devices if d["name"] == "厨房灯"), None)
    check("灯设备可写入数量 = 2", bool(light and light["supported_count"] == 2), str(light and light["supported_count"]))
    check("温度功能被识别为 sensor", bool(light and any(
        f["domain"] == "sensor" and f["kind"] == "sensor" for f in light["functions"])))
    check("重名被检出", result["duplicate_groups"] >= 1, str(result["duplicate_groups"]))
    check("重名明细含双路开关", any(d["device"] == "双路开关" for d in result["duplicates"]))
    check("设备排序：existing 在前、可接入次之",
          all(devices[i]["existing"] >= devices[i + 1]["existing"] for i in range(len(devices) - 1)))

    print("\n结果：", "全部通过" if not failures else f"{len(failures)} 项失败 {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
