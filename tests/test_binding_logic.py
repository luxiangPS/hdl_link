"""功能绑定纯逻辑自测：status 归一 + 触发判断 + 跟随下发（不依赖 HA）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link"))
import driver_logic as dl  # noqa: E402

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


def calls(info, status):
    return [c["service"] for c in dl.service_calls(info, status)]


print("[1] status 归一")
check("list 形态", dl.normalize_status([{"key": "on_off", "value": "on"}]) == [{"key": "on_off", "value": "on"}])
check("dict 形态", dl.normalize_status({"on_off": "off"}) == [{"key": "on_off", "value": "off"}])
check("空值", dl.normalize_status(None) == [])
check("缺 key 的项被丢弃", dl.normalize_status([{"value": "1"}]) == [])
check("数字转字符串", dl.normalize_status([{"key": "brightness", "value": 80}]) == [{"key": "brightness", "value": "80"}])

print("[2] 触发条件")
vals_on = {"on_off": "on"}
st = [{"key": "on_off", "value": "on"}]
check("any 放行", dl.should_fire("any", vals_on, st) is True)
check("on 命中", dl.should_fire("on", vals_on, st) is True)
check("off 不命中", dl.should_fire("off", vals_on, st) is False)
check("空 status 不放行", dl.should_fire("any", {}, []) is False)
check("非法 trigger 当 any", dl.should_fire("???", vals_on, st) is True)

print("[3] 跟随：面板按键 -> HA 灯")
info = {"entity_id": "light.keting", "spk": ""}
check("开灯", calls(info, [{"key": "on_off", "value": "on"}]) == ["light.turn_on"])
check("关灯", calls(info, [{"key": "on_off", "value": "off"}]) == ["light.turn_off"])
check("亮度", calls(info, [{"key": "brightness", "value": "55"}]) == ["light.turn_on"])
check("色温", calls(info, [{"key": "cct", "value": "3000"}]) == ["light.turn_on"])
check("亮度+开关都下", calls(info, [{"key": "on_off", "value": "on"}, {"key": "brightness", "value": "30"}]) == ["light.turn_on", "light.turn_on"])

print("[4] 跟随：窗帘 / 空调")
cover = {"entity_id": "cover.wushi", "spk": ""}
check("窗帘开", calls(cover, [{"key": "on_off", "value": "on"}]) == ["cover.open_cover"])
check("窗帘停", calls(cover, [{"key": "on_off", "value": "stop"}]) == ["cover.stop_cover"])
check("窗帘位置", calls(cover, [{"key": "percent", "value": "40"}]) == ["cover.set_cover_position"])
climate = {"entity_id": "climate.keting", "spk": ""}
check("空调温度", calls(climate, [{"key": "set_temp", "value": "24"}]) == ["climate.set_temperature"])
check("空调模式", calls(climate, [{"key": "mode", "value": "cool"}]) == ["climate.set_hvac_mode"])
check("空调风速", calls(climate, [{"key": "fan", "value": "high"}]) == ["climate.set_fan_mode"])

print("[5] 边界")
check("无 entity 不下发", calls({"entity_id": "", "spk": ""}, st) == [])
check("未知属性不下发", calls(info, [{"key": "xxx", "value": "1"}]) == [])

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
