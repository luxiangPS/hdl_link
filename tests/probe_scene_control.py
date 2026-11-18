"""真机验证场景控制 0xE01C：先读通道状态，发场景 1，再读状态对比。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _bootstrap  # noqa: F401,E402

from hdl_link.gateway import HdlGateway  # noqa: E402
from hdl_link.protocol import CMD_SCENE_CONTROL, build_scene_command  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "172.16.4.235"


async def main() -> None:
    gw = HdlGateway(HOST, 6000)
    await gw.async_start()
    try:
        before = await gw.async_poll_channels((10, 100))
        print("场景前 ch1-12:", [before.get(i) for i in range(1, 13)] if before else None)
        await gw.async_send(CMD_SCENE_CONTROL, (10, 254), build_scene_command(1))
        await asyncio.sleep(2.0)
        after = await gw.async_poll_channels((10, 100))
        print("场景后 ch1-12:", [after.get(i) for i in range(1, 13)] if after else None)
        changed = {
            ch: (before.get(ch), after.get(ch))
            for ch in (before or {})
            if before.get(ch) != (after or {}).get(ch)
        }
        print("通道变化:", changed or "无（该网关未配置场景 1，或场景未绑定通道）")
    finally:
        gw.close()


asyncio.run(main())
