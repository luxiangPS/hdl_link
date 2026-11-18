"""设备发现：一次性向整段地址发探测包，用一个时间窗收应答。

HDL 网关对每个 (subnet, device) 独立应答，所以 250 条探测包可以在毫秒级内发完，
再用 SCAN_WINDOW 秒统一收包，比逐个超时等待快两个数量级。

两类探测：
  * 0x0033（读通道状态，payload 0x01）→ 0x0034 → 判定为继电器/调光设备，payload[0] 即通道数
  * 0x1604（读温湿照，payload 0x01）  → 0x1605 → 判定为传感器设备
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Final

from .const import SCAN_DEVICE_RANGE, SCAN_WINDOW
from .gateway import HdlGateway
from .protocol import (
    CMD_CHANNELS_STATE,
    CMD_READ_CHANNELS,
    CMD_READ_SENSOR,
    CMD_SENSOR_REPORT,
    CMD_SENSOR_STATE,
    HdlFrame,
    parse_channel_states,
    parse_sensor_payload,
)

LOGGER = logging.getLogger(__name__)

PROBE_CODES: Final = (CMD_READ_CHANNELS, CMD_READ_SENSOR)


@dataclass(slots=True)
class DiscoveredDevice:
    """扫描命中一台设备。"""

    subnet: int
    device: int
    device_type: int
    channels: int = 0
    has_sensor: bool = False
    sensor: dict[str, float] = field(default_factory=dict)

    @property
    def address(self) -> str:
        return f"{self.subnet}.{self.device}"

    @property
    def kind(self) -> str:
        if self.channels:
            return "relay"
        if self.has_sensor:
            return "sensor"
        return "unknown"

    def describe(self) -> str:
        if self.channels:
            return f"{self.address} · {self.device_type} · {self.channels} 通道"
        if self.has_sensor:
            temp = self.sensor.get("temperature")
            lux = self.sensor.get("illuminance")
            extra = f" · {temp:.0f}°C / {lux:.0f}lx" if temp is not None and lux is not None else ""
            return f"{self.address} · {self.device_type} · 温湿照{extra}"
        return f"{self.address} · {self.device_type}"


async def async_scan(
    gateway: HdlGateway,
    subnet: int,
    first: int = 1,
    last: int = SCAN_DEVICE_RANGE,
    window: float = SCAN_WINDOW,
) -> dict[int, DiscoveredDevice]:
    """扫描 subnet.first..last，返回 {device_id: DiscoveredDevice}。"""
    found: dict[int, DiscoveredDevice] = {}

    def on_frame(frame: HdlFrame) -> None:
        if frame.sender[0] != subnet:
            return
        radio, dev, dtype = frame.sender
        entry = found.setdefault(dev, DiscoveredDevice(subnet=radio, device=dev, device_type=dtype))
        if not entry.device_type:
            entry.device_type = dtype
        if frame.code == CMD_CHANNELS_STATE:
            channels = len(parse_channel_states(frame.payload))
            if channels:
                entry.channels = max(entry.channels, channels)
        elif frame.code in (CMD_SENSOR_STATE, CMD_SENSOR_REPORT):
            entry.has_sensor = True
            entry.sensor = parse_sensor_payload(frame.payload)

    unsubscribe = gateway.add_listener(on_frame)
    try:
        for device in range(first, last + 1):
            for code in PROBE_CODES:
                await gateway.async_send(code, (subnet, device), b"\x01")
        await asyncio.sleep(window)
    finally:
        unsubscribe()

    LOGGER.debug("扫描 %s 子网 %s-%s 命中 %s 台设备", subnet, first, last, len(found))
    return found
