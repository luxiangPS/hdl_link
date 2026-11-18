"""HDL LINK over IP —— 报文编解码（纯标准库，无 Home Assistant 依赖）。

传输封装（UDP/6000，实测自 HDL 网关 192.168.1.150）：

    [0:4]    发送方 IP（网关回包时是网关自己的 IP，请求时填本机 IP）
    [4:14]   固定 ASCII  "HDLMIRACLE"
    [14:16]  固定 0xAA 0xAA
    [16:]    Buspro 内容：
               0    长度 = 9(头) + len(payload) + 2(CRC)
               1    发送方 subnet
               2    发送方 device id
               3:5  发送方 device type（大端）
               5:7  命令码（大端）
               7    目标 subnet
               8    目标 device id
               9..  payload
    [-2:]    CRC16-XMODEM（覆盖 [16:] 到 payload 末尾）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAGIC: Final = b"HDLMIRACLE"
MARK: Final = b"\xAA\xAA"
HEADER_LEN: Final = 9

# ---- 命令码（全部为真机实测命中的）----
CMD_CHANNEL_CONTROL: Final = 0x0031  # 单通道控制   payload: ch, level, time(u16, 0.1s)
CMD_CHANNEL_CONTROL_ACK: Final = 0x0032
CMD_READ_CHANNELS: Final = 0x0033  # 读通道状态   payload: count(0x01 = 全部)
CMD_CHANNELS_STATE: Final = 0x0034  # 应答         payload: count, level1..levelN
CMD_READ_LEVEL: Final = 0x0038
CMD_LEVEL_STATE: Final = 0x0039
CMD_READ_SCENE: Final = 0x000C
CMD_SCENE_STATE: Final = 0x000D
CMD_SCENE_CONTROL: Final = 0xE01C  # 场景控制（HDL LINK 场景号 1-128）
CMD_DRY_CONTACT: Final = 0x15D0  # 干接点广播   payload: area, channel, status(0=触发)
CMD_READ_SENSOR: Final = 5636  # 0x1604 读温湿照
CMD_SENSOR_STATE: Final = 5637  # 0x1605 应答   payload: success, value, lux(u16)
CMD_SENSOR_REPORT: Final = 5680  # 0x1630 主动上报（同上结构）
CMD_CURTAIN: Final = 0xE3E2  # 窗帘控制（无应答）
CMD_AC: Final = 0x1938  # 空调控制（无应答，本型号 0x0DE5 不实现）

CMD_NAMES: Final = {
    CMD_CHANNEL_CONTROL: "单通道控制",
    CMD_CHANNEL_CONTROL_ACK: "单通道控制应答",
    CMD_READ_CHANNELS: "读通道状态",
    CMD_CHANNELS_STATE: "读通道状态应答",
    CMD_READ_LEVEL: "读当前亮度",
    CMD_LEVEL_STATE: "读当前亮度应答",
    CMD_DRY_CONTACT: "干接点广播",
    CMD_READ_SENSOR: "读温湿照",
    CMD_SENSOR_STATE: "温湿照应答",
    CMD_SENSOR_REPORT: "温湿照上报",
    CMD_CURTAIN: "窗帘控制",
    CMD_AC: "空调控制",
}

# ---- 设备类型（大端字段里的 device type）----
DEV_TYPE_RELAY48: Final = 3557  # 0x0DE5，实测 48 通道继电器/调光
DEV_TYPE_SENSOR: Final = 1513  # 0x05E9，温湿照 + 干接点

DEV_TYPE_NAMES: Final = {
    DEV_TYPE_RELAY48: "48 通道继电器/调光主机",
    DEV_TYPE_SENSOR: "温湿度照度传感器",
}

# ---- 第三方发送方身份（HDL 网关接受任意 (subnet,id,type)，这里用一个不冲突的固定值）----
SENDER_SUBNET: Final = 253
SENDER_DEVICE: Final = 254
SENDER_TYPE: Final = 0xFFFE

DEFAULT_PORT: Final = 6000
DEFAULT_SUBNET: Final = 1
TEMPERATURE_OFFSET: Final = -20  # 实测：原始值 47 -> 27.0 °C


def crc16_xmodem(data: bytes) -> int:
    """CRC16-XMODEM：poly 0x1021，init 0，不反射。"""
    crc = 0
    for byte in data:
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_frame(
    source_ip: str,
    *,
    code: int,
    target: tuple[int, int],
    payload: bytes = b"",
    sender: tuple[int, int, int] = (SENDER_SUBNET, SENDER_DEVICE, SENDER_TYPE),
) -> bytes:
    """构造完整 UDP 报文。target=(subnet, device)。"""
    ip_bytes = bytes(int(part) for part in source_ip.split("."))
    header = bytearray(HEADER_LEN)
    header[0] = HEADER_LEN + len(payload) + 2
    header[1] = sender[0] & 0xFF
    header[2] = sender[1] & 0xFF
    header[3:5] = (sender[2] & 0xFFFF).to_bytes(2, "big")
    header[5:7] = (code & 0xFFFF).to_bytes(2, "big")
    header[7] = target[0] & 0xFF
    header[8] = target[1] & 0xFF
    content = bytes(header) + payload
    return ip_bytes + MAGIC + MARK + content + crc16_xmodem(content).to_bytes(2, "big")


@dataclass(slots=True, frozen=True)
class HdlFrame:
    """解析后的报文。"""

    source_ip: str
    sender: tuple[int, int, int]
    target: tuple[int, int]
    code: int
    payload: bytes

    @property
    def address(self) -> str:
        return f"{self.sender[0]}.{self.sender[1]}"

    @property
    def code_name(self) -> str:
        return CMD_NAMES.get(self.code, f"未知命令 0x{self.code:04X}")


class HdlFrameError(ValueError):
    """报文不合法（魔数/CRC/长度不符）。"""


def parse_frame(data: bytes) -> HdlFrame:
    """解析一个 UDP 报文，非法时抛 HdlFrameError。"""
    if len(data) < 16 + HEADER_LEN + 2:
        raise HdlFrameError("报文过短")
    if data[4:14] != MAGIC or data[14:16] != MARK:
        raise HdlFrameError("魔数不符")

    content = data[16:-2]
    if crc16_xmodem(content) != int.from_bytes(data[-2:], "big"):
        raise HdlFrameError("CRC 校验失败")

    declared = content[0]
    payload_end = declared - 2 if declared >= HEADER_LEN + 2 else len(content)
    return HdlFrame(
        source_ip=".".join(str(b) for b in data[0:4]),
        sender=(content[1], content[2], int.from_bytes(content[3:5], "big")),
        target=(content[7], content[8]),
        code=int.from_bytes(content[5:7], "big"),
        payload=content[HEADER_LEN:payload_end],
    )


# ---- payload 解析工具 ----


def build_channel_control(channel: int, level: int, ramp_tenth_sec: int = 0) -> bytes:
    """0x0031：channel(1-48), level(0-100), 渐变时间(0.1s 单位)。"""
    return bytes([channel & 0xFF, max(0, min(100, level)) & 0xFF,
                  (ramp_tenth_sec >> 8) & 0xFF, ramp_tenth_sec & 0xFF])


def parse_channel_states(payload: bytes) -> dict[int, int]:
    """0x0034：把 [count][level...] 解析成 {channel: level}。"""
    if not payload:
        return {}
    count = payload[0]
    levels = payload[1:1 + count]
    return {idx + 1: levels[idx] for idx in range(len(levels))}


def parse_sensor_payload(payload: bytes) -> dict[str, float]:
    """0x1605/0x1630：[success][value][lux:u16be]。"""
    result = {"success": float(payload[0]) if payload else 0.0}
    if len(payload) >= 2:
        result["temperature"] = float(payload[1] + TEMPERATURE_OFFSET)
    if len(payload) >= 4:
        result["illuminance"] = float(int.from_bytes(payload[2:4], "big"))
    return result


def build_curtain_command(channel: int, action: int) -> bytes:
    """0xE3E2：action 0=停 1=开 2=关（窗帘为脉冲式，无位置回读）。"""
    return bytes([channel & 0xFF, action & 0xFF])


def parse_scene_payload(payload: bytes) -> dict[int, int]:
    """0x000D：把 [数量][状态1]…[状态N] 解析成 {场景号: 状态}。"""
    if not payload:
        return {}
    count = payload[0]
    states = payload[1:1 + count]
    return {idx + 1: states[idx] for idx in range(len(states))}


def build_scene_command(scene: int) -> bytes:
    """0xE01C：场景号 1-128，payload 高位保留 0。"""
    scene = max(1, min(128, scene))
    return bytes([scene & 0xFF, 0x00])
