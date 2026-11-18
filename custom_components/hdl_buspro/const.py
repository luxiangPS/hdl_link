"""常量与配置键。"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hdl_buspro"
NAME: Final = "HDL Buspro"
MANUFACTURER: Final = "HDL / 河东科技"

CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_SUBNET: Final = "subnet"  # 扫描时使用的总线子网号
CONF_DEVICES: Final = "devices"  # 选中的设备地址列表，形如 ["10.100", "10.3"]
CONF_DEVICE_TYPES: Final = "device_types"  # {"10.100": "relay", "10.3": "sensor"}
CONF_DEVICE_TYPE_NUM: Final = "device_type_numbers"  # {"10.100": 3557}
CONF_CHANNELS: Final = "channels"  # {"10.100": 48} 通道数量（扫描时由 0x0034 应答得到）
CONF_DIMMERS: Final = "dimmers"  # 需要暴露成 light 的通道，形如 ["10.100/9", "10.100/10"]
CONF_COVERS: Final = "covers"  # 需要暴露成 cover（脉冲式窗帘）的通道，形如 ["10.100/23"]
CONF_DOORBELLS: Final = "doorbells"  # 需要暴露成门铃 binary_sensor 的设备，形如 ["10.3"]

DEFAULT_PORT: Final = 6000
DEFAULT_SCAN_SUBNET: Final = 1
SCAN_DEVICE_RANGE: Final = 250  # 扫描 1..250
SCAN_WINDOW: Final = 2.5  # 秒，发完探测包后收应答的时间窗
SCAN_PACKET_GAP: Final = 0.02  # 秒，发包间隔；真机实测 >50 包/秒会大量丢包

DEVICE_KIND_RELAY: Final = "relay"
DEVICE_KIND_SENSOR: Final = "sensor"

DEVICE_KINDS: Final = {
    DEVICE_KIND_RELAY: "继电器/调光设备",
    DEVICE_KIND_SENSOR: "温湿度照度传感器",
}

SIGNAL_UPDATE: Final = f"{DOMAIN}_update"
POLL_INTERVAL: Final = 10  # 秒，通道状态轮询周期（实测 10s 足够跟手）
SENSOR_POLL_INTERVAL: Final = 20

DOORBELL_EVENT_TIMEOUT: Final = 6  # 秒，干接点触发后保持 on 的时间
