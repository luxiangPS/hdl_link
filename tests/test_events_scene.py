"""场景事件端到端逻辑自测（用最小 hass 桩，不依赖真实 Home Assistant）。

覆盖本次修的 bug：场景事件的 status 是字符串 "on"，旧逻辑当"无状态"直接跳过，
绑定表里的 mode=action 规则永远不触发。
"""

import asyncio
import os
import sys
import types

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link")
sys.path.insert(0, BASE)


# ---------------------------------------------------------------- hass 桩
def _install_ha_stub():
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - 仅作类型占位
        pass

    core.HomeAssistant = HomeAssistant
    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")

    _DATA = {}

    class Store:
        def __init__(self, hass, version, key):
            self.key = key

        async def async_load(self):
            return _DATA.get(self.key)

        async def async_save(self, data):
            _DATA[self.key] = data

    storage.Store = Store
    helpers.storage = storage
    ha.core = core
    ha.helpers = helpers
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = storage
    return _DATA


DATA = _install_ha_stub()

# events.py 内部用相对导入，这里把包名做成可导入的
import importlib.util

pkg = types.ModuleType("hdlpkg")
pkg.__path__ = [BASE]
sys.modules["hdlpkg"] = pkg
spec = importlib.util.spec_from_file_location(
    "hdlpkg.events", os.path.join(BASE, "events.py"),
    submodule_search_locations=[BASE],
)
mod = importlib.util.module_from_spec(spec)
sys.modules["hdlpkg.events"] = mod
for name in ("driver_logic",):
    sub = importlib.util.spec_from_file_location(
        "hdlpkg.%s" % name, os.path.join(BASE, "%s.py" % name))
    m = importlib.util.module_from_spec(sub)
    sys.modules["hdlpkg.%s" % name] = m
    sub.loader.exec_module(m)
spec.loader.exec_module(mod)
events = mod

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


class Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append("%s.%s" % (domain, service))


class Hass:
    def __init__(self):
        self.services = Services()


SCENE = "0201036AFD43B20A0A0100011100"


def seed(binding):
    DATA[events.STORAGE_KEY] = {"sids": binding, "seen": [], "state": {}}


async def run_scene(hass, status="on"):
    obj = {"sid": SCENE, "status": status}
    return await events.async_handle(hass, [obj], kind="scene")


hass = Hass()

print("[1] 绑定 mode=action toggle，按一次场景")
seed({SCENE: {"entity_id": "light.ha_lamp", "mode": "action",
              "action": "toggle", "trigger": "any", "spk": "", "name": "回家走廊灯"}})
res = asyncio.run(run_scene(hass, "on"))
check("执行了 1 次 toggle",
      hass.services.calls == ["homeassistant.toggle"])
check("返回 executed 非空", bool(res.get("executed")))

print("[2] 连按两次（不能被状态去重吃掉）")
hass.services.calls.clear()
asyncio.run(run_scene(hass, "on"))
asyncio.run(run_scene(hass, "on"))
check("两次都执行", len(hass.services.calls) == 2)

print("[3] status 不是 on（场景没执行成功）不触发")
hass.services.calls.clear()
asyncio.run(run_scene(hass, "off"))
check("off 不执行", not hass.services.calls)

print("[4] mode=follow 的场景绑定给出警告、不执行")
hass.services.calls.clear()
seed({SCENE: {"entity_id": "light.ha_lamp", "mode": "follow",
              "action": "", "trigger": "any", "spk": "light.dimming", "name": "x"}})
asyncio.run(run_scene(hass, "on"))
check("follow 不执行（无属性可翻译）", not hass.services.calls)

print("[5] 未绑定的场景只记 seen，不执行")
hass.services.calls.clear()
seed({})
res = asyncio.run(run_scene(hass, "on"))
check("未绑定不执行", not hass.services.calls)
seen = (DATA[events.STORAGE_KEY].get("seen") or [])
check("场景 sid 记进 seen（面板下拉能看到）",
      any(item.get("sid") == SCENE for item in seen))

print("[6] logic 事件（status=success）同样触发")
hass.services.calls.clear()
LOGIC = "0201036AFD440A15150100011501"
seed({LOGIC: {"entity_id": "light.ha_lamp", "mode": "action",
              "action": "turn_on", "trigger": "any", "spk": "", "name": "逻辑"}})
asyncio.run(events.async_handle(hass, [{"sid": LOGIC, "status": "success", "enable": "true"}],
                                kind="logic"))
check("logic success → turn_on", hass.services.calls == ["homeassistant.turn_on"])

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
