"""发现层：网关自动发现 + 总线设备/功能扫描 + 场景读取。

三段能力：
  * async_discover_gateways —— 广播 + 本网段扫描，找出 UDP 6000 上应答的 HDL 网关
  * async_scan               —— 逐地址探测，识别设备类型/通道数/温湿照
  * async_read_scenes        —— 0x000C 读场景状态，0x000D 应答给出场景号与激活状态
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Final

from .const import (
    BUSPRO_PORT,
    DISCOVERY_SWEEP_LAST,
    DISCOVERY_WINDOW,
    SCAN_DEVICE_RANGE,
    SCAN_PACKET_GAP,
    SCAN_WINDOW,
)
from .gateway import HdlGateway
from .protocol import (
    CMD_CHANNELS_STATE,
    CMD_READ_CHANNELS,
    CMD_READ_SCENE,
    CMD_READ_SENSOR,
    CMD_SCENE_STATE,
    CMD_SENSOR_REPORT,
    CMD_SENSOR_STATE,
    DEV_TYPE_NAMES,
    HdlFrame,
    build_frame,
    parse_channel_states,
    parse_scene_payload,
    parse_sensor_payload,
)

LOGGER = logging.getLogger(__name__)

PROBE_CODES: Final = (CMD_READ_CHANNELS, CMD_READ_SENSOR)


# ---------------------------------------------------------------------------
# 网关发现
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GatewayCandidate:
    """广播/扫描命中的网关。"""

    host: str
    port: int = BUSPRO_PORT
    replies: int = 0

    @property
    def unique_id(self) -> str:
        return f"{self.host}:{self.port}"

    def describe(self) -> str:
        return f"{self.host} · UDP {self.port}（应答 {self.replies} 次）"


def _local_ipv4() -> str:
    """取本机默认出口 IP（不发包，仅让内核选路）。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        return probe.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        probe.close()


def _broadcast_targets(local_ip: str) -> list[str]:
    """广播地址候选：全网广播 + 本网段广播 + 本机地址。"""
    targets = ["255.255.255.255"]
    parts = local_ip.split(".")
    if len(parts) == 4 and parts[0] != "0":
        targets.append(".".join(parts[:3]) + ".255")
        targets.append(local_ip)
    return targets


async def async_discover_gateways(
    port: int = BUSPRO_PORT,
    window: float = DISCOVERY_WINDOW,
    sweep_last: int = DISCOVERY_SWEEP_LAST,
) -> list[GatewayCandidate]:
    """找出同一网段里应答的 HDL 网关。

    方法：
      1. 向全网广播 / 网段广播发一条只读探测（0x0033 读 1.1 通道），任何应答的
         源 IP 就是网关（HDL 网关用同一 IP 收发 UDP 6000）。
      2. 同时对本网段 /24 逐地址发探测（广播被防火墙挡住时兜底）。
    两条都不改设备状态，纯读。
    """
    loop = asyncio.get_running_loop()
    local_ip = _local_ipv4()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    # 优先用协议端口发，网关按源端口回包；被占用（调试工具/旧桥接）时退回随机端口
    try:
        sock.bind(("", port))
    except OSError:
        LOGGER.debug("UDP %s 被占用，改用随机源端口做发现", port)
        sock.bind(("", 0))
    sock.setblocking(False)
    candidates: dict[str, GatewayCandidate] = {}
    probe = build_frame(local_ip, code=CMD_READ_CHANNELS, target=(1, 1), payload=b"\x01")

    def _note(host: str, reply_port: int) -> None:
        item = candidates.setdefault(host, GatewayCandidate(host=host, port=port))
        item.replies += 1
        LOGGER.debug("发现网关候选 %s（第 %s 次应答，端口 %s）", host, item.replies, reply_port)

    # 收包任务
    async def _receive() -> None:
        while True:
            try:
                data, addr = await loop.sock_recvfrom(sock, 2048)
            except (BlockingIOError, InterruptedError):
                await asyncio.sleep(0.01)
                continue
            except OSError:
                return
            try:
                frame = parse_frame_loose(data)
            except Exception:  # noqa: BLE001 - 坏包直接忽略
                frame = None
            host = addr[0]
            if frame is not None or data[4:14] == b"HDLMIRACLE":
                _note(host, addr[1])

    receiver = asyncio.create_task(_receive())

    def _send(target: str, payload: bytes) -> None:
        try:
            sock.sendto(payload, (target, port))
        except OSError as err:
            LOGGER.debug("发往 %s:%s 失败：%s", target, port, err)

    try:
        for target in _broadcast_targets(local_ip):
            _send(target, probe)

        # 网段兜底扫描（只在本机是 /24 时做，最多 254 个地址）
        if len(local_ip.split(".")) == 4 and local_ip != "0.0.0.0":
            prefix = ".".join(local_ip.split(".")[:3])
            for last in range(1, min(sweep_last, 254) + 1):
                if f"{prefix}.{last}" == local_ip:
                    continue
                _send(f"{prefix}.{last}", probe)
                await asyncio.sleep(0.004)

        await asyncio.sleep(window)
    finally:
        receiver.cancel()
        try:
            await receiver
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        sock.close()

    result = sorted(candidates.values(), key=lambda c: (-c.replies, c.host))
    LOGGER.info("网关发现结束：%s 个候选 %s", len(result), [c.host for c in result])
    return result


def parse_frame_loose(data: bytes):
    """容错解析：只用来判断"是不是 HDL 报文"，解析失败返回 None。"""
    from .protocol import HdlFrameError, parse_frame

    try:
        return parse_frame(data)
    except HdlFrameError:
        return None


# ---------------------------------------------------------------------------
# 总线设备扫描
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DiscoveredDevice:
    """扫描命中一台设备。"""

    subnet: int
    device: int
    device_type: int
    channels: int = 0
    has_sensor: bool = False
    has_dry_contact: bool = False
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

    @property
    def type_name(self) -> str:
        return DEV_TYPE_NAMES.get(self.device_type, f"类型 {self.device_type}")

    def capabilities(self) -> str:
        parts: list[str] = [self.type_name]
        if self.channels:
            parts.append(f"{self.channels} 路通道（开关/调光）")
        if self.has_sensor:
            temp = self.sensor.get("temperature")
            lux = self.sensor.get("illuminance")
            if temp is not None and lux is not None:
                parts.append(f"温湿照 · 当前 {temp:.0f}°C / {lux:.0f}lx")
            else:
                parts.append("温湿度照度")
        if self.has_dry_contact:
            parts.append("干接点/门铃")
        return " · ".join(parts)

    def describe(self) -> str:
        return f"{self.address} · {self.capabilities()}"


async def async_scan(
    gateway: HdlGateway,
    subnet: int,
    first: int = 1,
    last: int = SCAN_DEVICE_RANGE,
    window: float = SCAN_WINDOW,
    gap: float = SCAN_PACKET_GAP,
) -> dict[int, DiscoveredDevice]:
    """扫描 subnet.first..last，返回 {device_id: DiscoveredDevice}。

    发包必须节流：真机实测网关在 >50 包/秒的突发下会大量丢包，按 SCAN_PACKET_GAP
    （默认 20ms）逐包发送后应答正常。
    """
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
        elif frame.code == 0x15D0:  # 干接点广播
            entry.has_dry_contact = True

    unsubscribe = gateway.add_listener(on_frame)
    try:
        for device in range(first, last + 1):
            for code in PROBE_CODES:
                await gateway.async_send(code, (subnet, device), b"\x01")
                if gap:
                    await asyncio.sleep(gap)
        await asyncio.sleep(window)
    finally:
        unsubscribe()

    LOGGER.debug("扫描子网 %s 的 %s-%s：命中 %s 台", subnet, first, last, len(found))
    return found


async def async_scan_auto(
    gateway: HdlGateway, subnets: tuple[int, ...] = (1, 10), window: float = SCAN_WINDOW
) -> tuple[int, dict[int, DiscoveredDevice]]:
    """依次尝试常见总线子网，返回 (命中的子网号, 设备字典)。

    HDL 现场绝大多数是子网 1；带楼层/分区的大型项目常把主机放在子网 10。
    用户不需要知道子网号，这里自动判定。
    """
    for subnet in subnets:
        found = await async_scan(gateway, subnet, window=window)
        if found:
            return subnet, found
    return subnets[0], {}


# ---------------------------------------------------------------------------
# 场景读取
# ---------------------------------------------------------------------------


async def async_read_scenes(
    gateway: HdlGateway, subnet: int, targets: list[int] | None = None, window: float = 1.5
) -> dict[int, int]:
    """读场景状态：0x000C 请求，0x000D 应答。

    应答 payload 形如 [数量][场景1状态][场景2状态]…，返回 {场景号: 状态}。
    场景是总线广播语义，任何设备的应答都收（不同主机回的号段可能不同，取并集）。
    """
    scenes: dict[int, int] = {}

    def on_frame(frame: HdlFrame) -> None:
        if frame.code != CMD_SCENE_STATE:
            return
        for scene, state in parse_scene_payload(frame.payload).items():
            scenes[scene] = max(scenes.get(scene, 0), state)

    unsubscribe = gateway.add_listener(on_frame)
    try:
        ask_targets = targets or [1, 254]
        for device in ask_targets:
            await gateway.async_send(CMD_READ_SCENE, (subnet, device), b"\x01")
            await asyncio.sleep(0.05)
        await asyncio.sleep(window)
    finally:
        unsubscribe()

    LOGGER.debug("子网 %s 读到 %s 个场景", subnet, len(scenes))
    return scenes
