"""事件路由自测：HDL 场景/逻辑上行帧必须走事件回调，不能当成属性上报。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

from protocol import LinkClient  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        FAILED.append(name)


def main():
    pushes, events = [], []
    lk = LinkClient("127.0.0.1", local_secret=None)
    lk.push_handler = pushes.append
    lk.event_handler = lambda topic, body: events.append((topic, body))

    print("[1] 场景事件（明文报文，模拟已解密）")
    body = {"objects": [{"sid": "0201036AFD43B20A0A0100011100", "name": "回家走廊灯", "status": "on"}]}
    raw = json.dumps(body, ensure_ascii=False).encode()
    lk._dispatch("/user/2109921361567219716/custom/scene/status/up", raw)
    check("触发事件回调", len(events) == 1, events)
    check("不进属性上报", pushes == [], pushes)
    check("带场景 sid", events and events[0][1]["objects"][0]["sid"].startswith("0201"))

    print("[2] 逻辑事件")
    events.clear()
    lk._dispatch("/user/1/custom/logic/status/up",
                 json.dumps({"objects": [{"sid": "l1", "status": "on"}]}).encode())
    check("逻辑事件也走事件回调", len(events) == 1, events)

    print("[3] 属性上报：既进 push，也进功能绑定")
    pushes.clear()
    events.clear()
    lk._dispatch("/base/2109921361567219716/thing/property/up",
                 json.dumps({"objects": [{"sid": "s1", "status": [{"key": "on_off", "value": "on"}]}]}).encode())
    check("属性上报也交给功能绑定", events and len(events) == 1, events)
    check("属性上报进 push_handler", len(pushes) == 1, pushes)

    print("[4] 未配置密钥的加密事件帧：不崩溃、不触发")
    events.clear()
    lk._dispatch("/user/1/custom/scene/status/up", b"\xfaW\x94\xc5" * 8)
    check("加密帧被安全跳过", events == [], events)

    print("[5] 网关下发 down：进事件回调（kind=down），不进 push")
    events.clear()
    pushes.clear()
    lk._dispatch("/base/010105003DF6F100/thing/property/down",
                 json.dumps({"objects": [{"sid": "0AD7B7738EDD83BC020100010101",
                                          "status": [{"key": "on_off", "value": "off"}]}]}).encode())
    check("down 交给事件回调", events and len(events) == 1, events)
    check("down 标记为 kind=down", events and events[0][1].get("kind") == "down", events)
    check("down 不进 push_handler", pushes == [], pushes)

    print("\n结果：%d 项失败" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
