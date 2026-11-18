"""真机验收：用「集成自带的客户端」直接对 HDL 网关做扫描 / 读 / 控制闭环。

与桥接方案的区别：这里不经过 MQTT、不经过 HA，直接验证集成代码本身。

前置条件（脚本会自己检查）：
  * 本机 UDP 6000 必须空闲 —— 关掉 HDL LINK Setup Tool / Buspro Studio，
    并先运行「停止真机桥接.bat」，否则网关只会把应答回给占着 6000 的那个程序。

判定四条（缺一不可）：
  1. 扫描能列出设备
  2. 能读回全部通道状态
  3. 下发控制后，网关广播里目标通道消失/出现（总线真值）
  4. 传感器返回温度与照度

用法：
  python tools/probe_real.py --host 192.168.1.150 --subnet 10 --channel 9
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import _bootstrap  # noqa: E402,F401

discovery = importlib.import_module("hdl_link.discovery")
gateway_mod = importlib.import_module("hdl_link.gateway")
protocol = importlib.import_module("hdl_link.protocol")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def port6000_free() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 6000))
        return True
    except OSError:
        return False
    finally:
        sock.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.1.150")
    parser.add_argument("--subnet", type=int, default=10)
    parser.add_argument("--channel", type=int, default=9, help="用来做开关验证的通道（默认 9）")
    parser.add_argument("--source-ip", default=None, help="本机与网关同网段的 IP")
    args = parser.parse_args()

    if not port6000_free():
        print("本机 UDP 6000 被占用，网关应答会被别的程序截走。")
        print("请先关闭 HDL LINK Setup Tool / Buspro Studio，并运行「停止真机桥接.bat」。")
        return 2

    client = gateway_mod.HdlGateway(args.host, 6000, source_ip=args.source_ip, timeout=2.0)
    await client.async_start()

    broadcasts: list[dict[int, int]] = []

    def on_frame(frame) -> None:  # noqa: ANN001
        if frame.code == protocol.CMD_CHANNELS_STATE:
            broadcasts.append(protocol.parse_channel_states(frame.payload))

    client.add_listener(on_frame)

    try:
        found = await discovery.async_scan(client, subnet=args.subnet, window=2.5)
        check("扫描发现设备", bool(found), ", ".join(f"{k}:{v.describe()}" for k, v in found.items()))

        relay_ids = [k for k, v in found.items() if v.channels]
        sensor_ids = [k for k, v in found.items() if v.has_sensor]
        if not relay_ids:
            check("存在继电器设备", False, "未发现带通道的设备")
            return 1
        relay = (args.subnet, relay_ids[0])

        states = await client.async_poll_channels(relay)
        check(
            "读回通道状态",
            bool(states) and len(states) > 0,
            f"{len(states or {})} 路，亮的通道 {[c for c, l in (states or {}).items() if l][:10]}",
        )

        print(f"\n>>> 对 {relay[0]}.{relay[1]} 通道{args.channel} 下发关闭（现场设备会动）")
        broadcasts.clear()
        await client.async_set_channel(relay, args.channel, 0)
        await asyncio.sleep(1.2)
        off_ok = any(args.channel not in b or b.get(args.channel, 0) == 0 for b in broadcasts)
        check("关：总线广播确认通道已关", off_ok, f"收到 {len(broadcasts)} 次通道广播")

        print(f">>> 恢复：通道{args.channel} 打开")
        broadcasts.clear()
        await client.async_set_channel(relay, args.channel, 100)
        await asyncio.sleep(1.2)
        on_ok = any(b.get(args.channel, 0) > 0 for b in broadcasts)
        check("开：总线广播确认通道已开", on_ok, f"收到 {len(broadcasts)} 次通道广播")

        if sensor_ids:
            reading = await client.async_read_sensor((args.subnet, sensor_ids[0]))
            check("读传感器", bool(reading), str(reading))
            check(
                "温度在合理范围",
                bool(reading and 0 <= reading.get("temperature", -99) <= 60),
                f"{reading.get('temperature') if reading else None} °C",
            )
        else:
            check("读传感器", False, "未发现传感器设备")
    finally:
        client.close()

    print()
    print(f"结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
