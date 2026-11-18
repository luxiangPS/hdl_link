"""端到端自测：不需要 Home Assistant、不需要真机。

启动模拟网关（127.0.0.1:6100），然后用集成自带的 HdlGateway + discovery 跑一遍：

  1. 扫描发现 2 台设备（10.100 48 通道 / 10.3 温湿照）
  2. 读 10.100 全部通道状态（48 路）
  3. 控制 20 通道开 → 网关广播 → 客户端缓存更新
  4. 读 10.3 温度/照度
  5. 收到门铃干接点广播

全部通过打印 PASS，否则 FAIL 并以非零码退出。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import _bootstrap  # noqa: E402,F401  （安装 hdl_link 命名空间包）

protocol = importlib.import_module("hdl_link.protocol")
gateway_mod = importlib.import_module("hdl_link.gateway")
discovery_mod = importlib.import_module("hdl_link.discovery")
sim = importlib.import_module("tests.sim_gateway")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


async def main() -> int:
    sim_gw = sim.SimGateway("127.0.0.1", 6100)
    await sim_gw.start()

    client = gateway_mod.HdlGateway(
        "127.0.0.1", 6100, bind_port=0, source_ip="127.0.0.1", timeout=2.0
    )
    await client.async_start()

    doors: list[int] = []
    client.add_listener(lambda f: doors.append(1) if f.code == protocol.CMD_DRY_CONTACT else None)

    try:
        # 1. 扫描
        found = await discovery_mod.async_scan(client, subnet=10, window=1.0)
        check("扫描发现 10.100 与 10.3", sorted(found) == [3, 100], f"found={sorted(found)}")
        relay = found.get(100)
        check("10.100 识别为 48 通道继电器", bool(relay and relay.channels == 48
             and relay.kind == "relay"), relay.describe() if relay else "无")

        # 2. 读通道
        states = await client.async_poll_channels((10, 100))
        check("读回 48 路通道", bool(states) and len(states) == 48, f"{len(states or {})} 路")
        check("初始 9/10 通道为 100", bool(states and states.get(9) == 100 and states.get(10) == 100))

        # 3. 控制
        await client.async_set_channel((10, 100), 20, 100)
        await asyncio.sleep(0.3)
        check("模拟网关确实收到 0x0031 并执行", sim_gw.levels.get(20) == 100, f"网关侧 ch20={sim_gw.levels.get(20)}")
        check("客户端缓存同步（广播回读）", client.channel_states.get((10, 100), {}).get(20) == 100)

        await client.async_set_channel((10, 100), 20, 0)
        await asyncio.sleep(0.3)
        check("关灯同样生效", sim_gw.levels.get(20) == 0 and
              client.channel_states.get((10, 100), {}).get(20) == 0)

        # 4. 传感器
        reading = await client.async_read_sensor((10, 3))
        check("读传感器成功", bool(reading),
              str(reading))
        check("温度 = 27.0 °C（偏移 -20）", bool(reading and reading.get("temperature") == 27.0))
        check("照度 = 300 lx", bool(reading and reading.get("illuminance") == 300.0))

        # 5. 门铃
        await asyncio.sleep(0.2)
        sim_gw.send_doorbell()
        await asyncio.sleep(0.3)
        check("收到干接点 0x15D0 广播", bool(doors))
    finally:
        client.close()
        sim_gw.close()

    print()
    print(f"结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
