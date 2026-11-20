"""join_bridge_rows 纯逻辑自测：场景 + 真实功能点 + 绑定表 + 事件统计 → 按键桥表。"""

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

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


SCENE = {"sid": "S1", "name": "HA:客厅灯", "status": "off"}
RELAY = {
    "sid": "R1",
    "name": "通用开关-201",
    "oid": "0201016AFD43BE04",
    "omodel": "MSMW24-BP.21",
    "spk": "on_off",
    "online": True,
}

print("[1] 场景 + 功能点 + 绑定 + 事件统计")
binds = {
    "S1": {"entity_id": "light.keting", "mode": "action", "action": "toggle", "trigger": "any"},
    "S9": {"entity_id": "light.gone"},  # 网关里已不存在
}
seen = [{"sid": "S1", "last_at": 1795177900, "count": 3, "last_status": "status=on", "kind": "scene"}]
out = events.join_bridge_rows([SCENE], [RELAY], binds, seen)
check("总行数 = 2", out["counts"]["total"] == 2)
check("场景 1 / 功能点 1", out["counts"]["scenes"] == 1 and out["counts"]["functions"] == 1)
check("已绑定 1", out["counts"]["bound"] == 1)
check("未绑定 1", out["counts"]["unbound"] == 1)
check("收到过事件 1", out["counts"]["fired"] == 1)
scene_row = out["rows"][0]
check("场景行带绑定实体", scene_row["entity_id"] == "light.keting")
check("场景行带事件时间与次数", scene_row["last_at"] == 1795177900 and scene_row["count"] == 3)
fn_row = out["rows"][1]
check("功能点行未绑定且无事件", fn_row["bound"] is False and fn_row["count"] == 0)
check("功能点行保留 oid/omodel", fn_row["oid"] == "0201016AFD43BE04" and fn_row["omodel"] == "MSMW24-BP.21")
check("孤儿绑定被单列", out["orphan_bindings"] == [{"sid": "S9", "entity_id": "light.gone"}])
check("unbound 里是功能点名", out["unbound"] == ["通用开关-201"])

print("[2] 空数据不炸")
empty = events.join_bridge_rows([], [], {}, None)
check(
    "空表结构完整",
    empty["rows"] == []
    and empty["counts"]["total"] == 0
    and empty["orphan_bindings"] == [],
)

print("[3] join_scene_bindings 兼容新签名（带 seen）")
out2 = events.join_scene_bindings([SCENE], binds, seen)
check("scene_list 也带事件统计", out2["scenes"][0]["count"] == 3)
check("scene_list 仍返回 unbound/orphan", "unbound" in out2 and "orphan_bindings" in out2)
out3 = events.join_scene_bindings([SCENE], binds)
check("不传 seen 也不报错", out3["scenes"][0]["count"] == 0)

print("[4] _remember 记时间与次数")
data = {"seen": []}
events._remember(data, "S1", {"on_off": "on"}, "scene")
check("首次 count=1 且有 last_at", data["seen"][0]["count"] == 1 and data["seen"][0]["last_at"])
events._remember(data, "S1", {"on_off": "off"}, "scene")
check("第二次 count=2 且状态更新",
      data["seen"][0]["count"] == 2 and data["seen"][0]["last_status"] == "on_off=off")

print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
