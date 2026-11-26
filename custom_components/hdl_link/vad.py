"""LINK 本地三方设备（A 设备）接入：不装网关驱动的「反向控制」通道。

官方文档《Link 协议接入》给出的本地三方对接流程（无需网关侧驱动文件）：

    1. 三方设备在 UDP 8585 上监听入网广播
       /user/all/custom/device/network_access/broadcast
    2. 收到广播后向网关发 /user/all/custom/device/network_access/request（UDP 点对点）
       网关代理认证后回 .../request_reply，带 gatewayId / oid / localSecret
    3. TCP 8586 连上网关，上报设备列表
       /user/ipc/custom/device/list/upload
    4. 上报功能列表
       /base/{oid}/thing/topo/list/upload
    5. 之后 Studio / App / 面板的控制会以 /base/{oid}/thing/property/down
       点对点推到这条 TCP 连接（这就是「不广播也能收到控制」的原因）
    6. 设备主动上报状态 /base/{oid}/thing/property/up

本模块只做协议与状态机，回调由调用方注入，不依赖 Home Assistant，便于单测。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:
    from .protocol import decode_frames, encode_frame, now_ms
except ImportError:  # 被 tools/ 或单测以顶层模块导入时走绝对导入
    from protocol import decode_frames, encode_frame, now_ms

LOGGER = logging.getLogger(__name__)

UDP_PORT = 8585
MULTICAST_ADDR = "239.0.168.188"
TCP_PORT = 8586

TOPIC_NET_ACCESS_BCAST = "/user/all/custom/device/network_access/broadcast"
TOPIC_NET_ACCESS_REQ = "/user/all/custom/device/network_access/request"
TOPIC_NET_ACCESS_REPLY = "/user/all/custom/device/network_access/request_reply"
TOPIC_DEVICE_UPLOAD = "/user/ipc/custom/device/list/upload"
TOPIC_TOPO_UPLOAD = "/base/{oid}/thing/topo/list/upload"
TOPIC_PROPERTY_DOWN = "/base/{oid}/thing/property/down"
TOPIC_PROPERTY_READ = "/base/{oid}/thing/property/read"
TOPIC_PROPERTY_UP = "/base/{oid}/thing/property/up"
TOPIC_TOPO_DELETE = "/base/{oid}/thing/topo/delete/notify"
TOPIC_DEVICE_HEARTBEAT = "/user/ipc/custom/device/heartbeat"

# spk -> 物模型两字节（1 byte 大类 + 1 byte 小类），用于自动生成 sid
SPK_MODEL_CODE: dict[str, str] = {
    "other.common": "0101",
    "panel.socket": "0102",
    "light.switch": "0201",
    "light.dimming": "0202",
    "light.cct": "0203",
    "light.rgb": "0204",
    "curtain.trietex": "0301",
    "curtain.shades": "0302",
    "curtain.roller": "0304",
    "sensor.temperature": "0502",
    "sensor.humidity": "0503",
    "sensor.light": "0504",
    "sensor.pir": "0501",
}

# spk -> 默认属性定义
SPK_ATTRS: dict[str, list[dict[str, Any]]] = {
    "light.switch": [{"key": "on_off", "data_type": "string", "value": ["on", "off"]}],
    "other.common": [{"key": "on_off", "data_type": "string", "value": ["on", "off"]}],
    "panel.socket": [{"key": "on_off", "data_type": "string", "value": ["on", "off"]}],
    "light.dimming": [
        {"key": "on_off", "data_type": "string", "value": ["on", "off"]},
        {"key": "brightness", "data_type": "integer", "min": 0, "max": 100},
    ],
    "curtain.trietex": [
        {"key": "on_off", "data_type": "string", "value": ["on", "off", "stop"]},
        {"key": "position", "data_type": "integer", "min": 0, "max": 100},
    ],
}


def sign_mac_key(mac: str, secret: str) -> str:
    """(mac+secret) 的两次 md5，官方入网 auth.mac_key 口径。"""
    first = hashlib.md5(f"{mac}{secret}".encode("utf-8")).hexdigest()
    return hashlib.md5(first.encode("utf-8")).hexdigest()


def make_sid(oid: str, spk: str, channel: int = 0) -> str:
    """按文档拼 sid：oid(8B) + 物模型(2B) + 通道(2B) + 大小类(2B) = 14B / 28 hex。"""
    model = SPK_MODEL_CODE.get(spk, "0101")
    return f"{oid}{model}{channel:04X}0101"


def ha_state_to_status(entity_id: str, spk: str, state: dict[str, Any] | None) -> dict[str, str]:
    """HA 实体状态 → HDL status（只填该 spk 认识的键）。"""
    if not state:
        return {}
    value = str(state.get("state", ""))
    attrs = state.get("attributes") or {}
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    out: dict[str, str] = {}

    if spk.startswith("curtain") or domain == "cover":
        if value == "open":
            out["on_off"] = "on"
        elif value == "closed":
            out["on_off"] = "off"
        elif value in ("on", "off"):
            out["on_off"] = value
        if attrs.get("current_position") is not None:
            out["position"] = str(attrs["current_position"])
        return out

    if domain in ("light", "switch", "fan", "input_boolean", "climate", "lock", "valve"):
        out["on_off"] = "on" if value == "on" else "off"
        if domain == "light" and attrs.get("brightness") is not None:
            out["brightness"] = str(round(int(attrs["brightness"]) / 255 * 100))
        return out

    if domain in ("sensor", "binary_sensor"):
        key = {
            "sensor.temperature": "temperature",
            "sensor.humidity": "humidity",
            "sensor.light": "illuminance",
            "sensor.co2": "co2",
            "sensor.pm25": "pm25",
            "sensor.tvoc": "tvoc",
        }.get(spk, "value")
        out[key] = value
    return out


@dataclass(slots=True)
class VadEntity:
    """一个要暴露给 HDL 的功能点。"""

    sid: str
    name: str
    spk: str
    entity_id: str = ""
    attributes: list[dict[str, Any]] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)

    def attributes_payload(self) -> list[dict[str, Any]]:
        return self.attributes or SPK_ATTRS.get(self.spk, SPK_ATTRS["other.common"])

    def status_payload(self) -> list[dict[str, str]]:
        return [{"key": k, "value": str(v)} for k, v in self.status.items()]


@dataclass(slots=True)
class VadDevice:
    """本进程在网关里对外呈现的那台「三方设备」。"""

    oid: str
    name: str
    model: str
    mac: str
    spk: str = "other.common"
    supplier: str = "HDL"
    fw_version: str = "1.0.0"
    hw_version: str = "1.0.0"
    image_id: str = "00000"


class VadError(Exception):
    """入网 / 上报 / 控制链路错误。"""


def _aes(data: bytes, key: bytes, *, mode: str, encrypt: bool) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(
        algorithms.AES(key),
        modes.ECB() if mode == "ecb" else modes.CBC(key),
    )
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def _unpad(plain: bytes) -> bytes:
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        return plain[:-pad]
    return plain


def decrypt_auto(raw: bytes, secret: str | None) -> dict[str, Any] | None:
    """把一帧 body 解成 dict：先当明文 JSON，再按 ECB / CBC 解密（两种口径都试）。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, UnicodeDecodeError):
        pass
    if not secret or len(secret) != 16 or len(raw) % 16:
        return None
    key = secret.encode("utf-8")
    for mode in ("ecb", "cbc"):
        try:
            plain = _unpad(_aes(raw, key, mode=mode, encrypt=False))
            parsed = json.loads(plain)
        except Exception:  # noqa: BLE001 - 解不开就换下一种
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


class VadBridge:
    """A 设备侧 LINK 客户端：入网 → 上报 → 收 down → 回 reply → 推 up。"""

    def __init__(
        self,
        host: str,
        device: VadDevice,
        entities: list[VadEntity],
        *,
        mac_secret: str | None = None,
        mac_key: str | None = None,
        tcp_port: int = TCP_PORT,
        udp_port: int = UDP_PORT,
        remote_udp_port: int | None = None,
        local_secret: str | None = None,
        on_down: Callable[[str, list[dict[str, str]]], Awable] | None = None,
        state_provider: Callable[[], dict[str, dict[str, str]]] | None = None,
        on_topo_delete: Callable[[str], Awable] | None = None,
    ) -> None:
        self.host = host
        self.device = device
        self.entities = entities
        self.mac_secret = (mac_secret or "").strip()
        self._mac_key = mac_key
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        # 网关侧 8585；本机与网关同机测试时可分开（真实部署两边都是 8585）
        self.remote_udp_port = remote_udp_port or udp_port
        self.local_secret = (local_secret or "").strip() or None
        self.on_down = on_down
        self.state_provider = state_provider
        self.on_topo_delete = on_topo_delete

        self.gateway_id = ""
        self.gateway_oid = ""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._udp: asyncio.DatagramProtocol | None = None

    # ------------------------------------------------------------------ 入网
    async def async_onboard(self, timeout: float = 6.0) -> dict[str, Any]:
        """UDP 8585 入网：等广播 / 主动发认证请求，拿到 gatewayId + localSecret。"""
        if self._mac_key is None:
            if not self.mac_secret:
                raise VadError("缺少 mac_secret（平台登记的 secret），无法计算 mac_key")
            self._mac_key = sign_mac_key(self.device.mac, self.mac_secret)

        loop = asyncio.get_running_loop()
        reply_fut: asyncio.Future = loop.create_future()
        state = {"authed": False}

        class Proto(asyncio.DatagramProtocol):
            def __init__(inner) -> None:  # noqa: N805 - 内部类，保持简洁
                inner.transport: asyncio.DatagramTransport | None = None

            def connection_made(inner, transport) -> None:  # noqa: N805
                inner.transport = transport

            def datagram_received(inner, data, addr) -> None:  # noqa: N805
                topic, body = _split_udp(data)
                if body is None:
                    return
                if topic == TOPIC_NET_ACCESS_REPLY and not reply_fut.done():
                    reply_fut.set_result(body)
                elif topic == TOPIC_NET_ACCESS_BCAST:
                    # 网关允许入网：只认自己的 mac，然后发起认证请求
                    slave_mac = str((body.get("objects") or {}).get("slave_mac") or "")
                    if slave_mac and slave_mac.lower() != self.device.mac.lower():
                        return
                    if state["authed"] or inner.transport is None:
                        return
                    state["authed"] = True
                    target = str((body.get("objects") or {}).get("ip_address")
                                 or body.get("ip_address") or self.host)
                    inner.transport.sendto(_udp_frame(TOPIC_NET_ACCESS_REQ, self._auth_body()),
                                           (target, self.remote_udp_port))
                    LOGGER.info("收到入网广播，已向 %s 发起认证请求", target)

        transport, proto = await loop.create_datagram_endpoint(
            Proto, local_addr=("0.0.0.0", self.udp_port), reuse_port=False,
            allow_broadcast=True,
        )
        self._udp = proto
        try:
            sock = transport.get_extra_info("socket")
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                struct.pack("4s4s", socket.inet_aton(MULTICAST_ADDR),
                                            socket.inet_aton("0.0.0.0")))
            except OSError:
                LOGGER.debug("加入组播 %s 失败（部分网卡不支持，可只用广播）", MULTICAST_ADDR)
            # 主动一脚：不等广播也能触发认证（网关侧已开启入网时有响应）
            payload = _udp_frame(TOPIC_NET_ACCESS_REQ, self._auth_body())
            for target in (self.host, "255.255.255.255", MULTICAST_ADDR):
                try:
                    transport.sendto(payload, (target, self.remote_udp_port))
                except OSError:
                    pass
            try:
                body = await asyncio.wait_for(reply_fut, timeout=timeout)
            except asyncio.TimeoutError as err:
                raise VadError(
                    "入网认证无响应：检查网关是否处于入网状态、设备 mac 是否已在平台登记"
                ) from err
        finally:
            transport.close()
            self._udp = None

        objects = body.get("objects") or {}
        auth = body.get("auth") or {}
        code = str(body.get("code", ""))
        if code not in ("200", "0"):
            raise VadError(f"入网被拒：code={code} {body.get('message', '')}".strip())
        self.gateway_id = str(objects.get("gatewayId", ""))
        self.gateway_oid = str(objects.get("oid", ""))
        secret = str(auth.get("localSecret") or "")
        if secret:
            self.local_secret = secret
        if objects.get("ip_address"):
            self.host = str(objects["ip_address"])
        LOGGER.info("入网成功 gatewayId=%s oid=%s 加密=%s",
                    self.gateway_id, self.gateway_oid, bool(self.local_secret))
        return body

    def _auth_body(self) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex[:12],
            "time_stamp": now_ms(),
            "objects": {
                "oid": self.device.oid,
                "device_mac": self.device.mac,
                "device_name": self.device.name,
                "device_model": self.device.model,
                "access_mode": "WIFI",
                "sid": make_sid(self.device.oid, self.device.spk),
                "gateway_type": self.device.spk,
                "ip_mac": self.device.mac,
            },
            "auth": {
                "mac_key": self._mac_key,
                "spk": self.device.spk,
                "request": {
                    "mac": self.device.mac,
                    "supplier": self.device.supplier,
                    "hardware_model": self.device.model,
                    "firmware_version": self.device.fw_version,
                },
            },
        }

    # ------------------------------------------------------------------ TCP
    async def async_connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.tcp_port), timeout=8
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise VadError(f"连接 {self.host}:{self.tcp_port} 失败: {err}") from err
        asyncio.create_task(self._read_loop())
        LOGGER.info("LINK TCP 已连接 %s:%s", self.host, self.tcp_port)

    async def async_close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except OSError:
                pass
            self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        buf = b""
        reader = self._reader
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buf += chunk
                frames, buf = decode_frames(buf)
                for topic, raw in frames:
                    try:
                        await self._dispatch(topic, raw)
                    except Exception as err:  # noqa: BLE001 - 单帧失败不能炸读循环
                        LOGGER.exception("处理帧 %s 失败: %s", topic, err)
        except (asyncio.CancelledError, OSError):
            pass
        finally:
            LOGGER.warning("LINK TCP 断开，等待重连")

    async def _dispatch(self, topic: str, raw: bytes) -> None:
        body = decrypt_auto(raw, self.local_secret)
        if body is None:
            LOGGER.debug("跳过无法解析的帧 %s len=%d", topic, len(raw))
            return
        msg_id = body.get("id")

        if topic.endswith("/thing/property/down"):
            await self._handle_down(body)
            return
        if topic.endswith("/thing/property/read"):
            await self._handle_read(body)
            return
        if topic.endswith("/thing/topo/delete/notify"):
            oid = ""
            for obj in body.get("objects") or []:
                oid = str(obj.get("oid") or "")
            if self.on_topo_delete is not None and oid:
                await self.on_topo_delete(oid)
            await self._send(TOPIC_TOPO_DELETE.format(oid=self.device.oid) + "_reply",
                             {"id": msg_id, "code": "200", "time_stamp": now_ms()})
            return
        if msg_id is not None:
            fut = self._pending.get(str(msg_id))
            if fut is not None and not fut.done():
                fut.set_result((topic, body))

    async def _handle_down(self, body: dict[str, Any]) -> None:
        objects = body.get("objects") or []
        if isinstance(objects, dict):
            objects = [objects]
        for obj in objects:
            sid = str(obj.get("sid") or "")
            status = [
                {"key": str(s.get("key")), "value": str(s.get("value"))}
                for s in (obj.get("status") or [])
                if isinstance(s, dict) and s.get("key") is not None
            ]
            if not status:
                continue
            LOGGER.info("下行控制 %s %s", sid, status)
            if self.on_down is not None:
                try:
                    await self.on_down(sid, status)
                except Exception as err:  # noqa: BLE001 - 执行失败也要回 reply，避免网关挂单
                    LOGGER.exception("执行下行失败 %s: %s", sid, err)
        await self._send(
            TOPIC_PROPERTY_DOWN.format(oid=self.device.oid) + "_reply",
            {"id": body.get("id"), "code": "200", "time_stamp": now_ms()},
        )

    async def _handle_read(self, body: dict[str, Any]) -> None:
        states = self.state_provider() if self.state_provider else \
            {e.sid: dict(e.status) for e in self.entities}
        items = []
        for obj in body.get("objects") or []:
            sid = str(obj.get("sid") or "")
            items.append({
                "sid": sid,
                "online": "true",
                "status": [{"key": k, "value": str(v)}
                           for k, v in states.get(sid, {}).items()],
            })
        await self._send(
            TOPIC_PROPERTY_READ.format(oid=self.device.oid) + "_reply",
            {"id": body.get("id"), "code": "200", "time_stamp": now_ms(), "objects": items},
        )

    # ------------------------------------------------------------------ 上报
    async def async_upload(self) -> None:
        """上报设备 oid 与全部功能 sid —— 之后 Studio/App 的设备表里就有这些卡片。"""
        device_obj = {
            "oid": self.device.oid,
            "device_name": self.device.name,
            "device_model": self.device.model,
            "device_mac": self.device.mac,
            "src": "LINK",
            "from": self.device.oid,
            "image_info": [{
                "imageId": self.device.image_id,
                "fw_version": self.device.fw_version,
                "hw_version": self.device.hw_version,
            }],
        }
        await self._request(TOPIC_DEVICE_UPLOAD, {"objects": [device_obj]},
                            expect=TOPIC_DEVICE_UPLOAD + "_reply")

        topo_items = []
        for ent in self.entities:
            topo_items.append({
                "sid": ent.sid,
                "name": ent.name,
                "spk": ent.spk,
                "oid": self.device.oid,
                "omodel": "",
                "online": "true",
                "from": self.device.oid,
                "attributes": ent.attributes_payload(),
                "status": ent.status_payload(),
            })
        await self._request(TOPIC_TOPO_UPLOAD.format(oid=self.device.oid),
                            {"objects": topo_items},
                            expect=TOPIC_TOPO_UPLOAD.format(oid=self.device.oid) + "_reply")
        LOGGER.info("已上报设备 %s 与 %d 个功能点", self.device.oid, len(topo_items))

    async def async_push(self, states: dict[str, dict[str, str]]) -> None:
        """把 HA 侧状态变化推给网关（Studio / App 状态跟随）。"""
        if not states:
            return
        items = [
            {"sid": sid, "status": [{"key": k, "value": str(v)} for k, v in st.items()]}
            for sid, st in states.items()
        ]
        await self._send(
            TOPIC_PROPERTY_UP.format(oid=self.device.oid),
            {"id": str(self._next_id()), "objects": items, "time_stamp": now_ms()},
        )

    async def async_heartbeat(self) -> None:
        try:
            await self._send(TOPIC_DEVICE_HEARTBEAT,
                             {"id": str(self._next_id()), "time_stamp": now_ms()})
        except (OSError, VadError):
            LOGGER.debug("心跳发送失败")

    # ------------------------------------------------------------------ 底层
    def _next_id(self) -> int:
        self._seq = (self._seq + 1) % 0x7FFFFFFF
        return self._seq

    async def _send(self, topic: str, body: dict[str, Any]) -> None:
        if self._writer is None:
            raise VadError("TCP 未连接")
        self._writer.write(encode_frame(topic, body))
        await self._writer.drain()

    async def _request(self, topic: str, body: dict[str, Any], *, expect: str,
                       timeout: float = 8.0) -> dict[str, Any]:
        msg_id = str(self._next_id())
        body = {**body, "id": msg_id, "time_stamp": now_ms()}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._send(topic, body)
            try:
                _t, resp = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError as err:
                raise VadError(f"{topic} 无响应（期望 {expect}）") from err
        finally:
            self._pending.pop(msg_id, None)
        code = str(resp.get("code", ""))
        if code not in ("200", "0"):
            raise VadError(f"{topic} 返回 code={code} {resp.get('message', '')}".strip())
        return resp



def _split_udp(data: bytes) -> tuple[str, dict[str, Any] | None]:
    marker = b"\r\n\r\n"
    idx = data.find(marker)
    if idx < 0:
        return "", None
    head = data[:idx].decode("utf-8", "ignore")
    topic = ""
    for line in head.split("\r\n"):
        if line.startswith("Topic:"):
            topic = line[len("Topic:"):].strip()
    try:
        body = json.loads(data[idx + len(marker):])
    except (ValueError, UnicodeDecodeError):
        return topic, None
    return topic, body if isinstance(body, dict) else None


def _udp_frame(topic: str, body: dict[str, Any]) -> bytes:
    """UDP 报文与 TCP 同格式（Topic/Length + JSON），用于入网握手。"""
    return encode_frame(topic, body)


async def async_probe_auth(host: str, device: VadDevice, secret: str,
                           udp_port: int = UDP_PORT, timeout: float = 6.0) -> dict[str, Any]:
    """只验证入网是否通过（不占 TCP），排障用。"""
    bridge = VadBridge(host, device, [], mac_secret=secret, udp_port=udp_port)
    try:
        return await bridge.async_onboard(timeout=timeout)
    finally:
        await bridge.async_close()





