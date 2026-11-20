"""场景/逻辑事件识别自测（driver_logic 纯函数）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link"))
import driver_logic as dl  # noqa: E402

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


print("[1] 事件类型识别")
check("scene 是事件", dl.is_event_kind("scene"))
check("logic 是事件", dl.is_event_kind("logic"))
check("function 不是事件", not dl.is_event_kind("function"))
check("down 不是事件", not dl.is_event_kind("down"))

print("[2] 场景事件字段提取（实测报文：status 是字符串）")
obj = {"sid": "0201036AFD43B20A0A0100011100", "status": "on"}
v = dl.event_values(obj)
check("status=on 被取出", v == {"status": "on"})
obj = {"sid": "0201036AFD440A15150100011501", "status": "success", "enable": "true"}
v = dl.event_values(obj)
check("logic success + enable", v == {"status": "success", "enable": "true"})
check("无 status 时为空", dl.event_values({"sid": "x"}) == {})

print("[3] normalize_status 对字符串不炸")
check("字符串 status 归一为空数组", dl.normalize_status("on") == [])

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
