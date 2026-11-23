"""新增设备类型的下行/上行口径自测（纯函数，不依赖 Home Assistant）。

覆盖 2.9.0 补进来的域：fan / lock / valve / vacuum / 窗帘 tilt / 暖通档位。
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

import bridge  # noqa: E402  （纯逻辑模块，不依赖 HA）
from driver_logic import service_calls  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("ok  ", name)
    else:
        print("FAIL", name, detail)
        FAILED.append(name)


def calls(entity, spk, status):
    return service_calls({"entity_id": entity, "spk": spk}, status)


# ---------------------------------------------------------------- 下行
c = calls("fan.wind", "hvac.fan", [{"key": "on_off", "value": "on"}])
check("风机 on → fan.turn_on", c and c[0]["service"] == "fan.turn_on", c)

c = calls("fan.wind", "hvac.fan", [{"key": "fan_speed_percent", "value": "4"}])
check("风机 4 档 → 50%", any(x.get("data", {}).get("percentage") == 50 for x in c), c)

c = calls("fan.wind", "light.dimming", [{"key": "percentage", "value": "60"}])
check("风机 percentage=60", any(x.get("data", {}).get("percentage") == 60 for x in c), c)

c = calls("lock.door", "other.common", [{"key": "on_off", "value": "on"}])
check("锁 on → lock.lock", c and c[0]["service"] == "lock.lock", c)

c = calls("lock.door", "other.common", [{"key": "on_off", "value": "off"}])
check("锁 off → lock.unlock", c and c[0]["service"] == "lock.unlock", c)

c = calls("valve.water", "other.common", [{"key": "on_off", "value": "on"}])
check("阀门 on → open_valve", c and c[0]["service"] == "valve.open_valve", c)

c = calls("vacuum.robot", "other.common", [{"key": "on_off", "value": "off"}])
check("扫地机 off → return_to_base", c and c[0]["service"] == "vacuum.return_to_base", c)

c = calls("cover.curtain", "curtain.trietex", [{"key": "angle", "value": "45"}])
check("百叶角度 → tilt 45", any(x.get("data", {}).get("tilt_position") == 45 for x in c), c)

c = calls("climate.ac", "hvac.ac", [{"key": "set_temp", "value": "26"}])
check("空调温度 → set_temperature", any(x.get("data", {}).get("temperature") == 26.0 for x in c), c)

c = calls("climate.ac", "hvac.ac", [{"key": "mode", "value": "heat"}])
check("空调模式 → set_hvac_mode", any(x.get("data", {}).get("hvac_mode") == "heat" for x in c), c)

# ---------------------------------------------------------------- 上行
s = bridge.status_from_state("hvac.fan", "on", {"percentage": 75})
keys = {x["key"]: x["value"] for x in s}
check("风机上送 fan_speed_percent=6", keys.get("fan_speed_percent") == "6", keys)

s = bridge.status_from_state("sensor.smoke", "on", {})
check("烟雾 on → alarm", {x["key"]: x["value"] for x in s}.get("smoke_status") == "alarm", s)

s = bridge.status_from_state("sensor.dryContact", "off", {})
check("干接点 off", {x["key"]: x["value"] for x in s}.get("on_off") == "off", s)

s = bridge.status_from_state("curtain.trietex", "open", {"current_position": 40, "current_tilt_position": 80})
kv = {x["key"]: x["value"] for x in s}
check("窗帘上送 position=40", kv.get("percent") == "40" or kv.get("position") == "40", kv)
check("窗帘上送 angle=80", kv.get("angle") == "80", kv)

# ---------------------------------------------------------------- 类型清单
check("hvac.ac 在可写入清单", "hvac.ac" in bridge.SPK_PROFILES)
check("烟雾 profile 存在", "sensor.smoke" in bridge.SPK_PROFILES)

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
