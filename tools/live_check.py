"""真机联通自检：直接使用集成内的连接层（不依赖 Home Assistant）。

用法：
    python live_check.py                 # 默认 192.168.1.150
    python live_check.py 192.168.1.150
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", "custom_components", "hdl_link"))

# 集成包 __init__ 依赖 Home Assistant，这里把纯连接层文件复制成临时包来导入
_tmp = tempfile.mkdtemp(prefix="hdl_link_core_")
_shim = os.path.join(_tmp, "hdl_link_core")
os.makedirs(_shim, exist_ok=True)
open(os.path.join(_shim, "__init__.py"), "w").close()
for _name in ("const.py", "protocol.py", "gateway.py", "discovery.py"):
    shutil.copy(os.path.join(PKG, _name), os.path.join(_shim, _name))
sys.path.insert(0, _tmp)

from hdl_link_core.const import SPK_PLATFORM  # noqa: E402
from hdl_link_core.gateway import HdlGateway  # noqa: E402


async def main(host: str) -> int:
    print("== 1. LINK 广播搜索（gateway/search）==")
    info = await HdlGateway.async_search(host)
    if info is None:
        print("  [失败] 网关无响应，检查 IP / TCP 8586")
        return 2
    print("  oid=%s  name=%s  model=%s  mac=%s  gatewayId=%s"
          % (info.oid, info.name, info.model, info.mac, info.gateway_id))
    print("  ip=%s" % info.raw.get("ip_address"))

    gw = HdlGateway(host, info.oid)
    await gw.async_connect()
    try:
        print("\n== 2. 功能清单（function/list/get）==")
        funcs = await gw.async_functions()
        print("  共 %d 个功能" % len(funcs))
        stats: dict[str, int] = {}
        for f in funcs:
            stats[f.spk] = stats.get(f.spk, 0) + 1
        for spk, count in sorted(stats.items(), key=lambda x: -x[1]):
            platform = SPK_PLATFORM.get(spk, ("(未映射)", ""))[0]
            print("  %-24s %3d 个  -> %s" % (spk, count, platform))

        print("\n== 3. 场景列表（scene/list/get）==")
        scenes = await gw.async_scenes()
        for s in scenes:
            print("  %s  %s  status=%s" % (s.get("sid"), s.get("name"), s.get("status")))
        if not scenes:
            print("  （网关内无场景）")

        print("\n== 4. 批量读取属性（property/read）==")
        sids = [f.sid for f in funcs]
        values = await gw.async_read_properties(sids)
        for f in funcs[:3]:
            print("  %-18s %-22s %s" % (f.name[:18], f.spk, values.get(f.sid)))
        print("  已读到 %d/%d 个功能的状态" % (len(values), len(sids)))

        target = next((f for f in funcs if f.spk == "light.switch"), None)
        if target is None:
            print("\n  没有 light.switch，跳过控制验证")
            return 0
        before = values.get(target.sid, {}).get("on_off")
        new_value = "off" if before == "on" else "on"
        print("\n== 5. 控制验证（property/down）==")
        print("  %s 当前 on_off=%s -> 下发 %s" % (target.name, before, new_value))
        ok = await gw.async_set_property(target.sid, [{"key": "on_off", "value": new_value}])
        print("  网关确认:", ok)
        await asyncio.sleep(1.2)
        after = (await gw.async_read_properties([target.sid])).get(target.sid, {})
        print("  回读:", after)
        if after.get("on_off") != new_value:
            print("  [失败] 下发未生效")
            return 3
        print("  恢复原状态 ->", before)
        await gw.async_set_property(target.sid, [{"key": "on_off", "value": str(before)}])
        await asyncio.sleep(1.2)
        final = (await gw.async_read_properties([target.sid])).get(target.sid, {})
        print("  最终回读:", final)
        print("\n[全部通过] LINK 协议层在真机上可用")
        return 0
    finally:
        await gw.async_close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.150"
    raise SystemExit(asyncio.run(main(host)))
