"""真机验证：网关广播发现 + 自动子网扫描 + 场景读取（不需要 Home Assistant）。

用法：
    python test_discovery_real.py            # 自动发现
    python test_discovery_real.py 192.168.1.150
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _bootstrap  # noqa: F401,E402

from hdl_link.discovery import (  # noqa: E402
    async_discover_gateways,
    async_read_scenes,
    async_scan_auto,
)
from hdl_link.gateway import HdlGateway  # noqa: E402


async def main() -> None:
    print("==== 1. 网关广播发现 ====")
    cands = await async_discover_gateways()
    for c in cands:
        print("  ", c.describe())
    if not cands:
        print("   广播/网段扫描未发现网关（6000 端口可能被调试工具或桥接占用）")

    host = sys.argv[1] if len(sys.argv) > 1 else (cands[0].host if cands else "192.168.1.150")
    print(f"\n==== 2. 自动子网扫描 {host} ====")
    gw = HdlGateway(host, 6000)
    try:
        await gw.async_start()
    except OSError as err:
        print("   绑定 6000 失败:", err)
        return
    try:
        subnet, found = await async_scan_auto(gw)
        print(f"   自动判定子网 = {subnet}，设备 {len(found)} 台")
        for d in found.values():
            print("  ", d.describe())

        print("\n==== 3. 场景读取（0x000C → 0x000D） ====")
        scenes = await async_read_scenes(gw, subnet)
        if scenes:
            print("   读到的场景:", scenes)
        else:
            print("   网关未返回场景状态（该主机可能未在 HDL 软件中配置场景）")
    finally:
        gw.close()


asyncio.run(main())
