"""状态同步纯逻辑自测：HA 状态 → HDL status 数组。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link"))
import bridge  # noqa: E402

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


def keys(status):
    return {s["key"]: s["value"] for s in status}


print("[1] 灯")
st = bridge.status_from_state("light.dimming", "on", {"brightness": 128})
d = keys(st)
check("on_off=on", d.get("on_off") == "on")
check("亮度 128/255 -> 50", d.get("brightness") == "50")
st = bridge.status_from_state("light.dimming", "off", {})
check("关灯只有 on_off", keys(st) == {"on_off": "off"})

print("[2] 开关 / 窗帘 / 空调")
check("开关", keys(bridge.status_from_state("other.common", "on", {})) == {"on_off": "on"})
d = keys(bridge.status_from_state("curtain.trietex", "open", {"current_position": 40}))
check("窗帘 percent", d.get("percent") == "40")
d = keys(bridge.status_from_state("hvac.ac", "on", {}))
check("空调不崩", isinstance(d, dict))

print("[3] 传感器")
d = keys(bridge.status_from_state("sensor.temperature", "23.5", {}))
check("温度两位小数", d.get("temperature") == "23.50")
d = keys(bridge.status_from_state("sensor.light", "120", {}))
check("照度", d.get("illuminance") == "120.00")
d = keys(bridge.status_from_state("sensor.pir", "on", {}))
check("人感 detected", d.get("people_status") == "detected")
d = keys(bridge.status_from_state("sensor.pir", "off", {}))
check("人感 undetected", d.get("people_status") == "undetected")
d = keys(bridge.status_from_state("sensor.doorwindow", "on", {}))
check("门磁 open", d.get("door_status") == "open")

print("[4] 边界")
check("unknown 不出数值", "temperature" not in keys(bridge.status_from_state("sensor.temperature", "unknown", {})))
check("不认识的 spk 返回空", bridge.status_from_state("nope", "on", {}) == [])
check("带 enable 的 spk 会补 enable", "enable" in keys(bridge.status_from_state("sensor.temperature", "20", {})))

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
