"""HA 驱动后端自测：下行 status → HA 服务调用（纯函数，不需要 HA 实例）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

import driver_logic as driver_bridge  # noqa: E402  （纯逻辑模块，不依赖 HA）

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        FAILED.append(name)


def calls(info, status):
    return [c["service"] for c in driver_bridge.service_calls(info, status)]


def main():
    print("[1] 开关类")
    check("灯开", calls({"entity_id": "light.a", "spk": "light.switch"},
                        [{"key": "on_off", "value": "on"}]) == ["light.turn_on"])
    check("灯关", calls({"entity_id": "light.a", "spk": "light.switch"},
                        [{"key": "on_off", "value": "off"}]) == ["light.turn_off"])
    check("插座开", calls({"entity_id": "switch.s", "spk": "panel.socket"},
                          [{"key": "on_off", "value": "on"}]) == ["switch.turn_on"])

    print("[2] 调光/色温")
    check("亮度→brightness_pct", calls({"entity_id": "light.b", "spk": "light.dimming"},
                                        [{"key": "brightness", "value": "40"}]) == ["light.turn_on"])
    c = driver_bridge.service_calls({"entity_id": "light.b", "spk": "light.dimming"},
                                    [{"key": "brightness", "value": "40"}])[0]
    check("亮度参数正确", c["data"].get("brightness_pct") == 40, c)
    c = driver_bridge.service_calls({"entity_id": "light.c", "spk": "light.cct"},
                                    [{"key": "cct", "value": "4000"}])[0]
    check("色温参数正确", c["data"].get("color_temp_kelvin") == 4000, c)

    print("[3] 窗帘三态 + 百分比")
    check("窗帘 on→open", calls({"entity_id": "cover.x", "spk": "curtain.roller"},
                                 [{"key": "on_off", "value": "on"}]) == ["cover.open_cover"])
    check("窗帘 stop", calls({"entity_id": "cover.x", "spk": "curtain.roller"},
                              [{"key": "on_off", "value": "stop"}]) == ["cover.stop_cover"])
    c = driver_bridge.service_calls({"entity_id": "cover.x", "spk": "curtain.roller"},
                                    [{"key": "percent", "value": "35"}])[0]
    check("窗帘位置参数", c["service"] == "cover.set_cover_position" and c["data"]["position"] == 35, c)

    print("[4] 暖通")
    check("空调开", calls({"entity_id": "climate.a", "spk": "hvac.ac"},
                          [{"key": "on_off", "value": "on"}]) == ["climate.turn_on"])
    combo = calls({"entity_id": "climate.a", "spk": "hvac.ac"},
                  [{"key": "set_temp", "value": "24.5"},
                   {"key": "mode", "value": "cool"},
                   {"key": "fan", "value": "auto"}])
    check("温度/模式/风速三连", combo == ["climate.set_temperature",
                                          "climate.set_hvac_mode",
                                          "climate.set_fan_mode"], combo)

    print("[5] 边界")
    check("空 entity 不产生调用", calls({"entity_id": "", "spk": "light.switch"},
                                        [{"key": "on_off", "value": "on"}]) == [])
    check("无效亮度不产生调用", calls({"entity_id": "light.d", "spk": "light.dimming"},
                                       [{"key": "brightness", "value": "abc"}]) == [])
    check("未知属性不崩", driver_bridge.service_calls(
        {"entity_id": "light.e", "spk": "light.switch"}, [{"key": "xyz", "value": "1"}]) == [])

    print("\n结果：%d 项失败" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
