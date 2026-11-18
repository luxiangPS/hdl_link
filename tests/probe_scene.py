"""场景读取/控制命令的真机探测：多目标 + 多 payload 形态。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _bootstrap  # noqa: F401,E402

from hdl_link.gateway import HdlGateway  # noqa: E402
from hdl_link.protocol import CMD_READ_SCENE, HdlFrame  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "172.16.4.235"


async def main() -> None:
    gw = HdlGateway(HOST, 6000)
    await gw.async_start()
    replies: list[HdlFrame] = []

    def on_frame(frame: HdlFrame) -> None:
        replies.append(frame)

    gw.add_listener(on_frame)
    try:
        cases = [(10, 100), (10, 3), (10, 254), (10, 1), (1, 1), (1, 254), (253, 254)]
        payloads = [b"\x01", b"\x00", b""]
        for target in cases:
            for payload in payloads:
                replies.clear()
                await gw.async_send(CMD_READ_SCENE, target, payload)
                await asyncio.sleep(0.9)
                got = [f for f in replies if f.code != 0x0034]
                tag = f"{target[0]}.{target[1]} payload={payload.hex() or '-'}"
                if got:
                    for f in got[:2]:
                        print(f"  {tag} → 0x{f.code:04X} sender={f.sender} {f.payload[:12].hex()}")
                else:
                    print(f"  {tag} → 无应答")
    finally:
        gw.close()


asyncio.run(main())
