"""join_scene_bindings 纯逻辑自测：场景清单 × 绑定表 → 排查表。"""

import importlib.util
import os
import sys
import types

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link")
sys.path.insert(0, BASE)

ha = types.ModuleType("homeassistant")
core = types.ModuleType("homeassistant.core")


class HomeAssistant:  # noqa: D401 - 仅作类型占位
    pass


core.HomeAssistant = HomeAssistant
helpers = types.ModuleType("homeassistant.helpers")
storage = types.ModuleType("homeassistant.helpers.storage")


class Store:
    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return {}

    async def async_save(self, data):
        pass


storage.Store = Store
helpers.storage = storage
ha.core = core
ha.helpers = helpers
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.core"] = core
sys.modules["homeassistant.helpers"] = helpers
sys.modules["homeassistant.helpers.storage"] = storage

pkg = types.ModuleType("hdlpkg")
pkg.__path__ = [BASE]
sys.modules["hdlpkg"] = pkg
sub = importlib.util.spec_from_file_location("hdlpkg.driver_logic", os.path.join(BASE, "driver_logic.py"))
m = importlib.util.module_from_spec(sub)
sys.modules["hdlpkg.driver_logic"] = m
sub.loader.exec_module(m)

spec = importlib.util.spec_from_file_location(
    "hdlpkg.events", os.path.join(BASE, "events.py"), submodule_search_locations=[BASE]
)
events = importlib.util.module_from_spec(spec)
sys.modules["hdlpkg.events"] = events
spec.loader.exec_module(events)


def run():
    scenes = [
        {"sid": "S1", "name": "回家走廊灯", "status": "off"},
        {"sid": "S2", "name": "看电影", "status": "on"},
    ]
    binds = {
        "S1": {"entity_id": "input_boolean.test", "mode": "action",
               "action": "toggle", "trigger": "any"},
        "S9": {"entity_id": "light.gone"},  # 网关里已不存在
    }
    out = events.join_scene_bindings(scenes, binds)
    assert out["count"] == 2, out
    assert out["bound"] == 1, out
    assert out["unbound"] == ["看电影"], out
    assert out["scenes"][0]["entity_id"] == "input_boolean.test", out
    assert out["scenes"][1]["bound"] is False, out
    assert out["orphan_bindings"] == [{"sid": "S9", "entity_id": "light.gone"}], out

    empty = events.join_scene_bindings([], {})
    assert empty == {"count": 0, "bound": 0, "unbound": [], "scenes": [], "orphan_bindings": []}, empty
    print("join_scene_bindings: 8 assertions passed")


if __name__ == "__main__":
    run()
