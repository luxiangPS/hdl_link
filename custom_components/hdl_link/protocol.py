"""HDL LINK 协议编解码与 TCP 客户端。

HDL 官方 Link 本地协议（配置走 TCP 8586，控制走 UDP；本集成统一走 TCP 8586，
因为网关在同一 TCP 信道上同时接受 property 控制报文）：

    Topic:/user/<gw_id>/custom/xxx\\r\\n
    Length: <body 字节数>\\r\\n
    \\r\\n
    {"id": "...", ...}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

_HEADER = re.compile(rb"Topic:(.*?)\r\nLength:\s*(\d+)\r\n\r\n")
_PORT = 8586
_UP_SUFFIX = "/thing/property/up"
# HDL 侧动作事件：场景执行、逻辑/按键动作，实测都会以加密帧推到客户端
_EVENT_SUFFIXES = ("/custom/scene/status/up", "/custom/logic/status/up")


def _is_event_topic(topic: str) -> bool:
    return any(topic.endswith(suffix) for suffix in _EVENT_SUFFIXES)


def decrypt_up_frame(raw: bytes, secret: str) -> dict[str, Any] | None:
    """解开网关的本地加密上报帧（AES-128-CBC，key=iv=本地密钥，整帧密文）。

    secret 由部署方在配置向导中提供（16 位）。失败时返回 None，由调用方回退到轮询值。
    """
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:  # pragma: no cover
        LOGGER.warning("缺少 cryptography，无法解析本地加密上报")
        return None
    key = secret.encode("utf-8")
    if len(key) != 16 or len(raw) % 16 != 0 or not raw:
        return None
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    plain = decryptor.update(raw) + decryptor.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    try:
        body = json.loads(plain.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        LOGGER.debug("上报帧解密后仍不是 JSON: %.80s", plain)
        return None
    return body if isinstance(body, dict) else None


def encode_frame(topic: str, body: dict[str, Any]) -> bytes:
    """把 topic + JSON body 封装成一帧。"""
    js = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    raw = js.encode("utf-8")
    head = "Topic:%s\r\nLength: %d\r\n\r\n" % (topic, len(raw))
    return head.encode("utf-8") + raw


def decode_frames(buf: bytes) -> tuple[list[tuple[str, bytes]], bytes]:
    """从字节流里切出完整帧，返回 (帧列表, 剩余未完成字节)。"""
    frames: list[tuple[str, bytes]] = []
    pos = 0
    while True:
        match = _HEADER.search(buf, pos)
        if match is None:
            break
        start = match.end()
        size = int(match.group(2))
        if len(buf) - start < size:
            # 帧不完整，保留从 match 起的原始数据
            return frames, buf[match.start():]
        frames.append((match.group(1).decode("utf-8", "ignore"), buf[start:start + size]))
        pos = start + size
    return frames, buf[pos:]


def now_ms() -> str:
    return str(int(time.time() * 1000))


class LinkError(Exception):
    """LINK 通信错误。"""


class LinkClient:
    """到 HDL 网关的 LINK TCP 客户端，自带编号/匹配与断线重连。"""

    def __init__(
        self,
        host: str,
        port: int = _PORT,
        timeout: float = 6.0,
        local_secret: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.local_secret = (local_secret or "").strip() or None
        # 网关主动上报（已解密）的回调：objects 列表
        self.push_handler: Callable[[list[dict[str, Any]]], None] | None = None
        # HDL 侧动作事件（场景执行 / 逻辑动作）回调，签名 (topic, body)
        self.event_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._push_queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=64)

    # ------------------------------------------------------------------ 连接
    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def async_connect(self) -> None:
        if self.connected:
            return
        # 连接超时跟随实例超时（网段探测时就要短，正常连接才用宽裕的上限）
        connect_timeout = min(self.timeout, 12.0)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=connect_timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise LinkError(f"连接 {self.host}:{self.port} 失败: {err}") from err
        self._reader_task = asyncio.create_task(self._async_reader())
        LOGGER.debug("HDL LINK 已连接 %s:%s", self.host, self.port)

    async def async_close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _async_reader(self) -> None:
        buf = b""
        assert self._reader is not None
        try:
            while True:
                chunk = await self._reader.read(65536)
                if not chunk:
                    break
                buf += chunk
                frames, buf = decode_frames(buf)
                for topic, raw in frames:
                    self._dispatch(topic, raw)
        except (asyncio.CancelledError, OSError):
            pass
        finally:
            writer, self._writer = self._writer, None
            if writer is not None:
                try:
                    writer.close()
                except OSError:
                    pass
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(LinkError("连接已断开"))
            self._pending.clear()

    def _dispatch(self, topic: str, raw: bytes) -> None:
        body: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
        except (ValueError, UnicodeDecodeError):
            body = None

        if body is None:
            # 网关主动上报为本地加密二进制，用配置的本地密钥解密后再当明文处理
            if (topic.endswith(_UP_SUFFIX) or _is_event_topic(topic)) and self.local_secret:
                body = decrypt_up_frame(raw, self.local_secret)
                if body is None:
                    LOGGER.debug("上报帧解密失败，长度 %d", len(raw))
                    return
            else:
                if topic.endswith(_UP_SUFFIX) or _is_event_topic(topic):
                    LOGGER.debug("收到加密上报但未配置本地密钥，跳过: %s", topic)
                return

        # HDL 侧动作事件（场景执行等）：交给事件回调，不当成属性上报
        if _is_event_topic(topic):
            if self.event_handler is not None:
                try:
                    self.event_handler(topic, body)
                except Exception as err:  # noqa: BLE001 - 回调异常不能影响读线程
                    LOGGER.debug("处理事件失败: %s", err)
            return

        msg_id = body.get("id")
        fut = self._pending.get(str(msg_id)) if msg_id is not None else None
        if fut is not None and not fut.done():
            fut.set_result((topic, body))
            return
        if topic.endswith("_reply"):
            LOGGER.debug("未匹配的响应 %s: %.200s", topic, raw)
            return
        # 功能点状态上报：既交给协调器刷新实体，也交给事件回调做「直接功能绑定」
        objects = body.get("objects")
        items = objects if isinstance(objects, list) else ([objects] if isinstance(objects, dict) else [])
        if items and topic.endswith(_UP_SUFFIX) and self.event_handler is not None:
            try:
                self.event_handler(topic, {"objects": items, "kind": "function"})
            except Exception as err:  # noqa: BLE001 - 回调异常不能影响读线程
                LOGGER.debug("处理功能上报失败: %s", err)

        # 主动上报：交给协调器刷新实体状态（无需 10s 轮询即可跟手）
        if objects and self.push_handler is not None:
            try:
                self.push_handler(objects if isinstance(objects, list) else [objects])
            except Exception as err:  # noqa: BLE001 - 回调异常不能影响读线程
                LOGGER.debug("处理上报失败: %s", err)

    # ------------------------------------------------------------------ 请求
    async def async_request(
        self,
        topic: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        retry: int = 1,
    ) -> dict[str, Any]:
        """发一帧并等待同 id 的响应。"""
        payload = dict(body or {})
        timeout = timeout or self.timeout
        last_err: Exception | None = None
        for attempt in range(retry + 1):
            try:
                await self.async_connect()
                async with self._lock:
                    self._seq = (self._seq + 1) % 0x7FFFFFFF
                    msg_id = str(self._seq)
                    payload["id"] = msg_id
                    payload.setdefault("time_stamp", now_ms())
                    fut: asyncio.Future = asyncio.get_running_loop().create_future()
                    self._pending[msg_id] = fut
                    assert self._writer is not None
                    self._writer.write(encode_frame(topic, payload))
                    await self._writer.drain()
                try:
                    resp_topic, resp = await asyncio.wait_for(fut, timeout=timeout)
                finally:
                    self._pending.pop(msg_id, None)
                if resp.get("code") not in (None, "0", "200", 0, 200):
                    LOGGER.debug("%s 返回 %s %s", resp_topic, resp.get("code"), resp.get("msg"))
                return resp
            except (OSError, LinkError, asyncio.TimeoutError) as err:
                last_err = err
                await self.async_close()
                if attempt >= retry:
                    break
                await asyncio.sleep(0.3)
        raise LinkError(f"请求 {topic} 失败: {last_err}")

    async def async_request_raw(
        self, topic: str, body: dict[str, Any] | None = None, **kw
    ) -> tuple[str, dict[str, Any]]:
        """返回 (响应 topic, 响应体)。"""
        payload = dict(body or {})
        # 复用 async_request 的匹配逻辑：这里单独实现以保留 topic
        timeout = kw.get("timeout") or self.timeout
        await self.async_connect()
        async with self._lock:
            self._seq += 1
            msg_id = str(self._seq)
            payload["id"] = msg_id
            payload.setdefault("time_stamp", now_ms())
            fut = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = fut
            assert self._writer is not None
            self._writer.write(encode_frame(topic, payload))
            await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, LinkError) as err:
            raise LinkError(f"请求 {topic} 超时: {err}") from err
        finally:
            self._pending.pop(msg_id, None)
