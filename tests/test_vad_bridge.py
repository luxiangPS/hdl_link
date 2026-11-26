"""无驱动方案（LINK 本地三方设备）端到端自检。

用本地仿真网关扮演 HDL 网关，跑完整七步：
接入认证 → 上报设备 → 上报功能 → 下发控制 → 回执 → 读状态 → 状态上行。

    python tests/test_vad_bridge.py
"""

import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

from protocol import decode_frames, encode_frame  # noqa: E402
from vad import (  # noqa: E402
    TOPIC_DEVICE_UPLOAD,
    TOPIC_NET_ACCESS_REPLY,
    TOPIC_NET_ACCESS_REQ,
    VadBridge,
    VadDevice,
    VadEntity,
    make_sid,
    sign_mac_key,
    ha_state_to_status,
    _split_udp,
)

FAILED = []
TCP_PORT = 18586
UDP_PORT = 18585          # 设备侧监听
GW_UDP_PORT = 18595       # 仿真网关侧监听（真实部署两边都是 8585）


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        FAILED.append(name)


def head(topic, body):
    return encode_frame(topic, body)


async def udp_gateway(ready: asyncio.Event, seen: dict):
    """UDP 接入：收到请求就回应答（认证通过 + 本地密钥）。"""

    class Proto(asyncio.DatagramProtocol):
        def connection_made(self, transport):  # noqa: N805
            self.transport = transport

        def datagram_received(self, data, addr):  # noqa: N805
            topic, body = _split_udp(data)
            if body is None:
                return
            seen["auth_topic"] = topic
            seen["auth_body"] = body
            reply = head(TOPIC_NET_ACCESS_REPLY, {
                "id": body.get("id"),
                "time_stamp": str(int(time.time() * 1000)),
                "code": "200",
                "objects": {"ip_address": "127.0.0.1", "gatewayId": "1111",
                            "oid": "010105003DF6F100"},
                "auth": {"localSecret": ""},
            })
            self.transport.sendto(reply, addr)
            ready.set()

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        Proto, local_addr=("127.0.0.1", GW_UDP_PORT))
    return transport


class TcpGateway:
    """仿真网关（连接侧）：收上报、下发控制与读取、校验应答。"""

    def __init__(self):
        self.frames: list[tuple[str, dict]] = []
        self.writer: asyncio.StreamWriter | None = None
        self.upload_event = asyncio.Event()
        self.down_reply: dict | None = None
        self.read_reply: dict | None = None
        self.up_body: dict | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.writer = writer
        buf = b""
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            buf += chunk
            frames, buf = decode_frames(buf)
            for topic, raw in frames:
                body = __import__("json").loads(raw)
                self.frames.append((topic, body))
                if topic == TOPIC_DEVICE_UPLOAD:
                    await self._reply(topic + "_reply", body.get("id"))
                elif topic.endswith("/thing/topo/list/upload"):
                    self.topos = body.get("objects") or []
                    await self._reply(topic + "_reply", body.get("id"))
                    self.upload_event.set()
                elif topic.endswith("/thing/property/down_reply"):
                    self.down_reply = body
                elif topic.endswith("/thing/property/read_reply"):
                    self.read_reply = body
                elif topic.endswith("/thing/property/up"):
                    self.up_body = body
                    await self._reply(topic + "_reply", body.get("id"))

    async def _reply(self, topic: str, msg_id):
        assert self.writer is not None
        self.writer.write(head(topic, {"id": msg_id, "code": "200",
                                       "time_stamp": str(int(time.time() * 1000))}))
        await self.writer.drain()

    async def send_down(self, sid: str, status: list[dict[str, str]]):
        assert self.writer is not None
        self.writer.write(head(f"/base/010105003DF6F100/thing/property/down", {
            "id": "d1", "time_stamp": str(int(time.time() * 1000)),
            "objects": [{"sid": sid, "status": status}],
        }))
        await self.writer.drain()

    async def send_read(self, sids: list[str]):
        assert self.writer is not None
        self.writer.write(head("/base/010105003DF6F100/thing/property/read", {
            "id": "r1", "time_stamp": str(int(time.time() * 1000)),
            "objects": [{"sid": sid} for sid in sids],
        }))
        await self.writer.drain()


async def main() -> int:
    print("[1] 工具函数")
    check("sid 长度 28 hex", len(make_sid("04E8030100000A01", "light.dimming", 1)) == 28)
    check("不同 spk 物模型不同",
          make_sid("04E8030100000A01", "light.switch", 1) != make_sid("04E8030100000A01", "curtain.trietex", 1))
    check("mac_key 稳定", sign_mac_key("02AABBCC0102", "s3cret") == sign_mac_key("02AABBCC0102", "s3cret"))
    check("MAC 归一（冒号/大写）",
          "".join(c for c in "02:AA:BB:CC:01:02".lower() if c in "0123456789abcdef") == "02aabbcc0102")

    print("[1b] HA 状态 → HDL status")
    check("灯开+亮度",
          ha_state_to_status("light.a", "light.dimming",
                             {"state": "on", "attributes": {"brightness": 153}})
          == {"on_off": "on", "brightness": "60"})
    check("窗帘位置",
          ha_state_to_status("cover.a", "curtain.trietex",
                             {"state": "open", "attributes": {"current_position": 60}})
          == {"on_off": "on", "position": "60"})
    check("开关关",
          ha_state_to_status("switch.a", "other.common", {"state": "off", "attributes": {}})
          == {"on_off": "off"})
    check("温度传感器",
          ha_state_to_status("sensor.a", "sensor.temperature",
                             {"state": "23.5", "attributes": {}}) == {"temperature": "23.5"})

    print("[2] 入网 → 上报 → 控制 → 上行")
    seen: dict = {}
    udp_ready = asyncio.Event()
    udp_transport = await udp_gateway(udp_ready, seen)
    gw = TcpGateway()
    server = await asyncio.start_server(gw.handle, "127.0.0.1", TCP_PORT)

    device = VadDevice(oid="04E8030100000A01", name="HA Bridge", model="HA-BRIDGE.430",
                       mac="02AABBCC0102")
    entities = [
        VadEntity(sid=make_sid(device.oid, "light.dimming", 1), name="客厅灯",
                  spk="light.dimming", entity_id="light.demo"),
        VadEntity(sid=make_sid(device.oid, "curtain.trietex", 2), name="窗帘",
                  spk="curtain.trietex", entity_id="cover.demo"),
    ]
    got: list[tuple[str, list[dict[str, str]]]] = []

    async def on_down(sid, status):
        got.append((sid, status))

    bridge = VadBridge("127.0.0.1", device, entities, mac_secret="s3cret",
                       tcp_port=TCP_PORT, udp_port=UDP_PORT, remote_udp_port=GW_UDP_PORT,
                       on_down=on_down,
                       state_provider=lambda: {entities[0].sid: {"on_off": "on", "brightness": "60"}})
    await bridge.async_onboard(timeout=3)
    check("走了 network_access/request", seen.get("auth_topic") == TOPIC_NET_ACCESS_REQ, seen.get("auth_topic"))
    check("入网拿到 gatewayId", bridge.gateway_id == "1111", bridge.gateway_id)
    check("认证请求带 mac_key", (seen.get("auth_body") or {}).get("auth", {}).get("mac_key") is not None)

    await bridge.async_connect()
    await bridge.async_upload()
    await asyncio.wait_for(gw.upload_event.wait(), timeout=3)
    check("功能列表上报了 2 条", len(gw.topos) == 2, len(gw.topos))
    check("online 标为 true", all(t["online"] == "true" for t in gw.topos))
    check("from 填自身 oid", all(t["from"] == device.oid for t in gw.topos))
    check("调光灯带 brightness 属性",
          any(a["key"] == "brightness" for a in gw.topos[0]["attributes"]))
    check("窗帘带 position 属性",
          any(a["key"] == "position" for a in gw.topos[1]["attributes"]))

    await gw.send_down(entities[0].sid, [{"key": "on_off", "value": "on"},
                                         {"key": "brightness", "value": "40"}])
    await asyncio.sleep(0.2)
    check("收到下行控制", bool(got), got)
    check("下行 status 解析正确",
          got and [s["key"] for s in got[0][1]] == ["on_off", "brightness"], got)
    check("down_reply code=200", (gw.down_reply or {}).get("code") == "200", gw.down_reply)

    await gw.send_read([entities[1].sid])
    await asyncio.sleep(0.2)
    check("read_reply 回 online", (gw.read_reply or {}).get("objects", [{}])[0].get("online") == "true")

    await bridge.async_push({entities[0].sid: {"on_off": "on", "brightness": "60"}})
    await asyncio.sleep(0.2)
    check("上行 property/up 到达网关", (gw.up_body or {}).get("objects", [{}])[0].get("sid") == entities[0].sid)

    await bridge.async_close()
    server.close()
    udp_transport.close()

    print("\n结果：%d 项失败" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


