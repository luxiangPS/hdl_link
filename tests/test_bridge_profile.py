"""bridge.py 自测：五类物模型注册表、oid/sid 生成、值换算。

不依赖 Home Assistant，可直接 python tests/test_bridge_profile.py 跑。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

import bridge  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    print("[1] 五类物模型覆盖")
    for spk in (
        "panel.socket", "panel.button",
        "light.switch", "light.dimming", "light.cct", "light.rgb",
        "sensor.temperature", "sensor.humidity", "sensor.pir", "sensor.doorwindow",
        "curtain.switch", "curtain.roller", "curtain.trietex",
        "hvac.ac", "hvac.fan", "hvac.airFresh",
    ):
        check(spk, spk in bridge.SPK_PROFILES)
    for platform in ("panel", "light", "sensor", "curtain", "hvac"):
        got = [k for k, v in bridge.SPK_PROFILES.items() if v["platform"] == platform]
        check("平台 %s 有物模型" % platform, bool(got), got)

    print("[2] 属性格式：网关实测口径")
    attrs = bridge.function_attributes("light.switch")
    on_off = next(a for a in attrs if a["key"] == "on_off")
    check("on_off 是 string+枚举", on_off["data_type"] == "string" and on_off["value"] == ["on", "off"], on_off)
    cct = next(a for a in bridge.function_attributes("light.cct") if a["key"] == "cct")
    check("色温键是 cct", cct["key"] == "cct" and cct["data_type"] == "integer")
    cur = next(a for a in bridge.function_attributes("curtain.roller") if a["key"] == "percent")
    check("窗帘是 percent", cur["key"] == "percent")
    curtain_on = next(a for a in bridge.function_attributes("curtain.roller") if a["key"] == "on_off")
    check("窗帘 on_off 三态", curtain_on["value"] == ["on", "off", "stop"], curtain_on)
    check("暖通 set_temp 是 float", any(
        a["key"] == "set_temp" and a["data_type"] == "float"
        for a in bridge.function_attributes("hvac.ac")))

    print("[3] oid/sid 生成")
    oid1 = bridge.device_oid("abc-device")
    oid2 = bridge.device_oid("abc-device")
    oid3 = bridge.device_oid("other-device")
    check("oid 16 位", len(oid1) == 16, oid1)
    check("oid 稳定", oid1 == oid2)
    check("oid 不撞", oid1 != oid3)
    sid = bridge.function_sid(oid1, 1)
    check("sid 28 位", len(sid) == 28, sid)
    check("sid 带 oid 前缀", sid.startswith(oid1))
    check("sid 序号递增不同", bridge.function_sid(oid1, 1) != bridge.function_sid(oid1, 2))

    print("[4] 物理模型标识")
    dev = bridge.device_payload(oid1, "light", "abc-device", "客厅灯")
    check("device_name=HA", dev["device_name"] == "HA", dev["device_name"])
    check("device_model=HA.light", dev["device_model"] == "HA.light", dev["device_model"])
    check("无 manufacturer 字段", "manufacturer" not in dev)
    check("device_mac 12 位", len(dev["device_mac"]) == 12, dev["device_mac"])

    print("[5] 值换算")
    check("bool True→on", bridge.to_hdl_value("light.switch", "on_off", True) == "on")
    check("bool False→off", bridge.to_hdl_value("light.switch", "on_off", False) == "off")
    check("brightness 255→100", bridge.to_hdl_value("light.dimming", "brightness", 255) == "100")
    check("brightness 128→50", bridge.to_hdl_value("light.dimming", "brightness", 128) == "50")
    check("cct 4000→4000", bridge.to_hdl_value("light.cct", "cct", 4000) == "4000")
    check("percent 42", bridge.to_hdl_value("curtain.roller", "percent", 42) == "42")
    check("温度两位小数", bridge.to_hdl_value("sensor.temperature", "temperature", 23.4) == "23.40")
    check("unknown→None", bridge.to_hdl_value("sensor.temperature", "temperature", "unavailable") is None)
    check("pir detected", bridge.to_hdl_value("sensor.pir", "people_status", "on") == "detected")
    check("门磁 open", bridge.to_hdl_value("sensor.doorwindow", "door_status", "on") == "open")
    check("窗帘 open→on", bridge.to_hdl_value("curtain.roller", "on_off", "open") == "on")

    print("[6] spk 推断")
    check("sensor 按 device_class", bridge.spk_for_entity("sensor", "humidity") == "sensor.humidity")
    check("binary_sensor 门磁", bridge.spk_for_entity("binary_sensor", "door") == "sensor.doorwindow")
    check("light 默认开关灯", bridge.spk_for_entity("light") == "light.switch")
    check("light 支持颜色", bridge.spk_for_entity("light", None, 64) == "light.rgbw")
    check("cover→卷帘", bridge.spk_for_entity("cover") == "curtain.roller")
    check("climate→空调", bridge.spk_for_entity("climate") == "hvac.ac")
    check("插座", bridge.spk_for_entity("switch", "outlet") == "panel.socket")

    print("\n结果：%d 项失败" % len(FAILED))
    if FAILED:
        print("失败项:", ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
