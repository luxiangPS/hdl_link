"""HDL 网关 UDP 客户端（纯 asyncio，不依赖 Home Assistant，方便单测/压测）。"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any, Final

from .protocol import (
    CMD_CHANNEL_CONTROL,
    CMD_CHANNELS_STATE,
    CMD_READ_CHANNELS,
    CMD_READ_SENSOR,
    CMD_SENSOR_REPORT,
    CMD_SENSOR_STATE,
    HdlFrame,
    HdlFrameError,
    build_channel_control,
    build_frame,
    parse_channel_states,
    parse_frame,
    parse_sensor_payload,
)

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = 1.5
EventCallback = Callable[[HdlFrame], None]

# 请求-应答配对：把请求命令映射到它可能的应答码
RESPONSE_CODES: Final[dict[int, tuple[int, ...]]] = {
    CMD_READ_CHANNELS: (CMD_CHANNELS_STATE,),
    CMD_READ_SENSOR: (CMD_SENSOR_STATE,),
}


class HdlGatewayError(Exception):
    """网关通信错误。"""


class HdlGateway:
    """一个 UDP 6000 网关连接。

    一个进程/一条总线上只能有一个客户端占用（实测网关应答只回给绑定 6000 的那个 socket），
    因此本类设计为「一个网关一个实例」。
    """

    def __init__(
        self,
        host: str,
        port: int = 6000,
        *,
        source_ip: str | None = None,
        bind_port: int | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        # 真机实测：网关会把应答回给绑定 6000 的客户端，因此默认绑 6000；
        # 仿真/测试时传 bind_port=0 由系统分配临时端口。
        self.bind_port = port if bind_port is None else bind_port
        self.timeout = timeout
        self._source_ip = source_ip
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _HdlProtocol | None = None
        self._pending: dict[tuple[tuple[int, int], int], asyncio.Future[HdlFrame]] = {}
        self._listeners: list[EventCallback] = []
        # 状态缓存：{(subnet, device): {channel: level}}
        self.channel_states: dict[tuple[int, int], dict[int, int]] = {}
        self.sensor_states: dict[tuple[int, int], dict[str, float]] = {}

    # ---------------- 生命周期 ----------------

    @property
    def started(self) -> bool:
        return self._protocol is not None

    async def async_start(self) -> None:
        """绑定本地 UDP 端口并开始收包。"""
        if self.started:
            return
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            _HdlProtocol,
            local_addr=("0.0.0.0", self.bind_port),
            allow_broadcast=True,
        )
        self._transport = transport
        self._protocol = protocol
        protocol.owner = self
        socket_: socket.socket = transport.get_extra_info("socket")
        try:
            socket_.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:  # pragma: no cover - 平台差异
            LOGGER.debug("无法开启广播选项，忽略")
        LOGGER.debug("HDL 网关客户端已启动，本地端口 %s", self.port)

    def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._protocol = None

    # ---------------- 收发 ----------------

    @property
    def _my_ip(self) -> str:
        if self._source_ip:
            return self._source_ip
        if self._transport is not None:
            sockname = self._transport.get_extra_info("sockname")
            if sockname:
                local = sockname[0]
                if local and local != "0.0.0.0":
                    return local
        return "127.0.0.1"

    def _send_raw(self, code: int, target: tuple[int, int], payload: bytes = b"") -> None:
        if self._transport is None:
            raise HdlGatewayError("客户端尚未启动")
        frame = build_frame(self._my_ip, code=code, target=target, payload=payload)
        self._transport.sendto(frame, (self.host, self.port))

    async def async_send(
        self, code: int, target: tuple[int, int], payload: bytes = b""
    ) -> None:
        """只发不等应答（用于控制类命令）。"""
        self._send_raw(code, target, payload)

    async def async_request(
        self,
        code: int,
        target: tuple[int, int],
        payload: bytes = b"",
    ) -> HdlFrame:
        """发一条请求并等待对应应答；超时抛 HdlGatewayError。"""
        expected = RESPONSE_CODES.get(code, (code | 0x1,))
        loop = asyncio.get_running_loop()
        key = (target, expected[0])
        future: asyncio.Future[HdlFrame] = loop.create_future()
        self._pending[key] = future
        try:
            self._send_raw(code, target, payload)
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError as err:
            raise HdlGatewayError(
                f"{target[0]}.{target[1]} 在 {self.timeout}s 内没有应答命令 0x{code:04X}"
            ) from err
        finally:
            self._pending.pop(key, None)

    def _on_frame(self, frame: HdlFrame) -> None:
        self._cache_frame(frame)

        for (target, expected_code), future in list(self._pending.items()):
            if frame.code == expected_code and (
                frame.address == f"{target[0]}.{target[1]}" or frame.target == target
            ):
                if not future.done():
                    future.set_result(frame)
                return

        for listener in self._listeners:
            try:
                listener(frame)
            except Exception:  # noqa: BLE001 - 监听器异常不能拖垮接收循环
                LOGGER.exception("HDL 事件监听器异常")

    def _cache_frame(self, frame: HdlFrame) -> None:
        addr = (frame.sender[0], frame.sender[1])
        if frame.code == CMD_CHANNELS_STATE:
            self.channel_states[addr] = parse_channel_states(frame.payload)
        elif frame.code in (CMD_SENSOR_STATE, CMD_SENSOR_REPORT):
            self.sensor_states[addr] = parse_sensor_payload(frame.payload)

    # ---------------- 事件 ----------------

    def add_listener(self, callback: EventCallback) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    # ---------------- 业务方法 ----------------

    async def async_poll_channels(self, target: tuple[int, int]) -> dict[int, int] | None:
        """读一个继电器设备全部通道状态；失败返回 None 并把错误写日志。"""
        try:
            frame = await self.async_request(CMD_READ_CHANNELS, target, b"\x01")
        except HdlGatewayError as err:
            LOGGER.warning("读取 %s.%s 通道状态失败: %s", *target, err)
            return None
        return parse_channel_states(frame.payload)

    async def async_set_channel(
        self, target: tuple[int, int], channel: int, level: int, ramp_tenth_sec: int = 0
    ) -> None:
        """设置单个通道亮度（0=关，1..100=开/亮度）。"""
        await self.async_send(
            CMD_CHANNEL_CONTROL,
            target,
            build_channel_control(channel, level, ramp_tenth_sec),
        )
        # 本地乐观更新，等下一次轮询用总线真值覆盖
        self.channel_states.setdefault(target, {})[channel] = max(0, min(100, level))

    async def async_read_sensor(self, target: tuple[int, int]) -> dict[str, float] | None:
        """读一个温湿照传感器的数值。"""
        try:
            frame = await self.async_request(CMD_READ_SENSOR, target, b"\x01")
        except HdlGatewayError as err:
            LOGGER.warning("读取 %s.%s 传感器失败: %s", *target, err)
            return None
        return parse_sensor_payload(frame.payload)


class _HdlProtocol(asyncio.DatagramProtocol):
    """把 UDP 数据报交给 HdlGateway。"""

    owner: HdlGateway | None = None

    def datagram_received(self, data: bytes, addr: Any) -> None:  # noqa: ANN401
        try:
            frame = parse_frame(data)
        except HdlFrameError as err:
            LOGGER.debug("丢弃非法报文 %s: %s", addr, err)
            return
        if self.owner is not None:
            self.owner._on_frame(frame)  # noqa: SLF001 - 同模块内协作

    def error_received(self, exc: Exception) -> None:
        LOGGER.debug("UDP 错误: %s", exc)
