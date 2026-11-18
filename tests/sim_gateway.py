"""HDL Buspro 模拟网关（用于无真机调试集成）。

监听 127.0.0.1:6100，按真机实测的报文格式应答：

  设备 10.100（type 3557，48 通道，初始只有 9 通道在亮）
  设备 10.3  （type 1513，温度 27.0°C / 照度 300lx）

支持：0x0033 读通道 / 0x0031 控制（并主动广播 0x0034）/ 0x1604 读传感器 /
0x15D0 干接点（每 DOORBELL_INTERVAL 秒广播一次触发）。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import _bootstrap  # noqa: E402,F401  （安装 hdl_buspro 命名空间包）

from hdl_buspro.protocol import (  # noqa: E402
    CMD_CHANNELS_STATE,
    CMD_DRY_CONTACT,
    CMD_READ_CHANNELS,
    CMD_READ_SENSOR,
    CMD_SENSOR_STATE,
    HdlFrame,
    build_frame,
    parse_frame,
)

LOGGER = logging.getLogger("sim_gateway")

RELAY_ADDR = (10, 100)
RELAY_TYPE = 3557
RELAY_CHANNELS = 48
SENSOR_ADDR = (10, 3)
SENSOR_TYPE = 1513
DOORBELL_INTERVAL = 15.0


class SimGateway(asyncio.DatagramProtocol):
    """一个最小可用的 HDL 网关。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6100) -> None:
        self.host = host
        self.port = port
        self.levels: dict[int, int] = {9: 100, 10: 100}
        self.temperature_raw = 47  # 47 - 20 = 27.0 °C
        self.lux = 300
        self._transport: asyncio.DatagramTransport | None = None
        self.clients: set[tuple[str, int]] = set()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: self, local_addr=(self.host, self.port)
        )
        self._transport = transport
        sock: socket.socket = transport.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        LOGGER.info("模拟网关已在 %s:%s 启动", self.host, self.port)

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def _reply(self, code: int, target_addr: tuple[int, int], payload: bytes, to) -> None:  # noqa: ANN001
        frame = build_frame(
            self.host,
            code=code,
            target=target_addr,
            payload=payload,
            sender=(RELAY_ADDR[0], target_addr[1], RELAY_TYPE),
        )
        self._transport.sendto(frame, to)  # type: ignore[union-attr]

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.clients.add(addr)
        try:
            frame: HdlFrame = parse_frame(data)
        except ValueError:
            return

        target = frame.target
        if frame.code == CMD_READ_CHANNELS:
            if target == RELAY_ADDR:
                levels = [self.levels.get(ch, 0) for ch in range(1, RELAY_CHANNELS + 1)]
                self._reply(CMD_CHANNELS_STATE, target, bytes([RELAY_CHANNELS, *levels]), addr)
            return

        if frame.code == CMD_READ_SENSOR:
            if target == SENSOR_ADDR:
                payload = bytes([1, self.temperature_raw]) + self.lux.to_bytes(2, "big")
                self._reply(CMD_SENSOR_STATE, target, payload, addr)
            return

        if frame.code == 0x0031 and target == RELAY_ADDR:
            channel, level = frame.payload[0], frame.payload[1]
            self.levels[channel] = level
            print(f"[模拟网关] 执行 通道{channel} -> {level}")
            asyncio.get_running_loop().call_later(0.05, self.broadcast_channels)
            return

    def broadcast_channels(self) -> None:
        """像真机一样主动广播一次完整通道状态。"""
        if self._transport is None:
            return
        levels = [self.levels.get(ch, 0) for ch in range(1, RELAY_CHANNELS + 1)]
        payload = bytes([RELAY_CHANNELS, *levels])
        frame = build_frame(
            self.host,
            code=CMD_CHANNELS_STATE,
            target=(0, 0),
            payload=payload,
            sender=(RELAY_ADDR[0], RELAY_ADDR[1], RELAY_TYPE),
        )
        for client in self.clients:
            self._transport.sendto(frame, client)

    def send_doorbell(self) -> None:
        """广播一次干接点触发（area=8, channel=1, status=0）。"""
        if self._transport is None:
            return
        frame = build_frame(
            self.host,
            code=CMD_DRY_CONTACT,
            target=(0, 0),
            payload=bytes([8, 1, 0]),
            sender=(SENSOR_ADDR[0], SENSOR_ADDR[1], SENSOR_TYPE),
        )
        for client in self.clients:
            self._transport.sendto(frame, client)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    gw = SimGateway()
    await gw.start()
    print("模拟网关运行中：127.0.0.1:6100，Ctrl+C 退出")
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        gw.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
