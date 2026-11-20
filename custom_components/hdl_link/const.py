"""HDL LINK 集成常量：LINK 协议主题、SPK 类型映射与实体属性键。"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hdl_link"
NAME: Final = "HDL LINK"
MANUFACTURER: Final = "HDL（河东科技）"
MODEL_PREFIX: Final = "HDL LINK"

# ------------------------------------------------------------------ 配置键
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_GW_ID: Final = "gw_id"          # 网关 oid，LINK topic 中的 ${gw_id}
CONF_GW_NAME: Final = "gw_name"
CONF_ENTITIES: Final = "entities"    # [{"sid","spk","oid","name"}]
CONF_SCENES: Final = "scenes"        # [{"sid","name"}]
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_LOCAL_SECRET: Final = "local_secret"   # 可选：网关本地加密密钥，用于解析实时上报

DEFAULT_PORT: Final = 8586
UDP_SEARCH_PORT: Final = 8585           # 本地搜索（广播）端口
DEFAULT_POLL_INTERVAL: Final = 0       # 秒；0 = 不做周期轮询，状态由网关主动上报
MIN_POLL_INTERVAL: Final = 0
MAX_POLL_INTERVAL: Final = 120

# ------------------------------------------------------------------ LINK 主题
TOPIC_GATEWAY_SEARCH: Final = "/user/all/custom/gateway/search"
TOPIC_GATEWAY_GET: Final = "/user/{gw}/custom/gateway/get"
TOPIC_HEARTBEAT: Final = "/user/{gw}/custom/gateway/heartbeat"
TOPIC_DEVICE_LIST: Final = "/user/{gw}/custom/device/list/get"
TOPIC_FUNCTION_LIST: Final = "/user/{gw}/custom/function/list/get"
TOPIC_FUNCTION_ATTR: Final = "/user/{gw}/custom/function/attribute/get"
TOPIC_ROOM_LIST: Final = "/user/{gw}/custom/room/list/get"
TOPIC_SCENE_LIST: Final = "/user/{gw}/custom/scene/list/get"
TOPIC_SCENE_EXECUTE: Final = "/user/{gw}/custom/scene/execute"
TOPIC_PROPERTY_READ: Final = "/base/{gw}/thing/property/read"
TOPIC_PROPERTY_DOWN: Final = "/base/{gw}/thing/property/down"
# 上行属性上报：网关只认这条主题判定功能点在线（实测：上报后 online=true、status 有值）
TOPIC_PROPERTY_UP: Final = "/base/{gw}/thing/property/up"

# ------------------------------------------------------- 设备/功能 增删改（写入网关）
# 对应 HDL《LINK主网关协议<->调试软件》：先开编辑开关，再增删改物理模型与功能点
TOPIC_REMOTE_CONFIG_EDIT: Final = "/user/{gw}/custom/remoteconfig/edit"
TOPIC_DEVICE_ADD: Final = "/user/{gw}/custom/device/add"
TOPIC_DEVICE_DELETE: Final = "/user/{gw}/custom/device/delete"
TOPIC_DEVICE_EDIT: Final = "/user/{gw}/custom/device/edit"
TOPIC_FUNCTION_ADD: Final = "/user/{gw}/custom/function/attribute/add"
TOPIC_FUNCTION_EDIT: Final = "/user/{gw}/custom/function/attribute/edit"
TOPIC_FUNCTION_DELETE: Final = "/user/{gw}/custom/function/attribute/delete"
TOPIC_TOPO_FOUND: Final = "/base/{gw}/thing/topo/found"

# 驱动名：Buspro 驱动 / LINK(a) 驱动；写入第三方设备挂在 "a" 驱动下
DRIVER_A: Final = "a"
PROVISION_VERSION: Final = "2.2.0"
STORAGE_KEY: Final = "hdl_link.provision"
STORAGE_VERSION: Final = 1

# 可供选择的功能类型（spk）与展示名：来自 SPK_PLATFORM，再补几个写入用的
PROVISIONABLE_SPK: Final[tuple[str, ...]] = (
    "light.switch",
    "light.dimming",
    "light.cct",
    "light.rgb",
    "light.rgbw",
    "panel.socket",
    "other.common",
    "curtain.switch",
    "curtain.trietex",
    "curtain.roller",
    "sensor.temperature",
    "sensor.humidity",
    "sensor.light",
    "sensor.co2",
    "sensor.pm25",
    "sensor.pir",
    "sensor.doorwindow",
)

# 每个 spk 需要的属性定义（写入 function/attribute/add 的 attributes）
SPK_ATTRIBUTE_SCHEMA: Final[dict[str, list[dict[str, str]]]] = {
    "light.switch": [{"key": "on_off", "data_type": "boolean"}],
    "light.dimming": [
        {"key": "on_off", "data_type": "boolean"},
        {"key": "brightness", "data_type": "int", "min": "0", "max": "100"},
    ],
    "light.cct": [
        {"key": "on_off", "data_type": "boolean"},
        {"key": "brightness", "data_type": "int", "min": "0", "max": "100"},
        {"key": "color_temp", "data_type": "int", "min": "2700", "max": "6500"},
    ],
    "light.rgb": [
        {"key": "on_off", "data_type": "boolean"},
        {"key": "brightness", "data_type": "int", "min": "0", "max": "100"},
        {"key": "rgb", "data_type": "string"},
    ],
    "light.rgbw": [
        {"key": "on_off", "data_type": "boolean"},
        {"key": "brightness", "data_type": "int", "min": "0", "max": "100"},
        {"key": "rgb", "data_type": "string"},
        {"key": "white", "data_type": "int", "min": "0", "max": "100"},
    ],
    "panel.socket": [{"key": "on_off", "data_type": "boolean"}],
    "other.common": [{"key": "on_off", "data_type": "boolean"}],
    "curtain.switch": [{"key": "on_off", "data_type": "boolean"}],
    "curtain.trietex": [{"key": "position", "data_type": "int", "min": "0", "max": "100"}],
    "curtain.roller": [{"key": "position", "data_type": "int", "min": "0", "max": "100"}],
    "sensor.temperature": [{"key": "temperature", "data_type": "float", "min": "-40", "max": "120"}],
    "sensor.humidity": [{"key": "humidity", "data_type": "float", "min": "0", "max": "100"}],
    "sensor.light": [{"key": "illuminance", "data_type": "float", "min": "0", "max": "100000"}],
    "sensor.co2": [{"key": "co2", "data_type": "float", "min": "0", "max": "5000"}],
    "sensor.pm25": [{"key": "pm25", "data_type": "float", "min": "0", "max": "1000"}],
    "sensor.pir": [{"key": "people_status", "data_type": "boolean"}],
    "sensor.doorwindow": [{"key": "door_status", "data_type": "boolean"}],
}

# 扫描时跳过的域（系统/不可控，写进 HDL 没有意义）
SCAN_SKIP_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "update",
        "persistent_notification",
        "person",
        "sun",
        "zone",
        "tag",
        "tts",
        "stt",
        "conversation",
        "assist_satellite",
        "notify",
        "event",
        "todo",
        "device_tracker",
        "weather",
        "number",
        "select",
        "text",
        "time",
        "date",
        "datetime",
        "image",
    }
)

# 扫描时纳入的域（顺序即 UI 展示顺序）
SCAN_DOMAINS: Final[tuple[str, ...]] = (
    "light",
    "switch",
    "cover",
    "climate",
    "fan",
    "media_player",
    "water_heater",
    "humidifier",
    "lock",
    "vacuum",
    "sensor",
    "binary_sensor",
)

SIGNAL_UPDATE: Final = f"{DOMAIN}_update"

# ------------------------------------------------------------------ 类型映射
# spk -> (平台, 展示名)
SPK_PLATFORM: Final[dict[str, tuple[str, str]]] = {
    # 照明
    "light.switch": ("light", "开关灯"),
    "light.dimming": ("light", "调光灯"),
    "light.cct": ("light", "色温灯"),
    "light.rgb": ("light", "RGB 灯"),
    "light.rgbw": ("light", "RGBW 灯"),
    # 开关 / 插座 / 继电器触点
    "panel.socket": ("switch", "插座"),
    "other.common": ("switch", "通用开关"),
    # 传感器
    "sensor.temperature": ("sensor", "温度"),
    "sensor.humidity": ("sensor", "湿度"),
    "sensor.light": ("sensor", "照度"),
    "sensor.co2": ("sensor", "CO₂"),
    "sensor.pm25": ("sensor", "PM2.5"),
    "sensor.tvoc": ("sensor", "TVOC"),
    # 二元传感器
    "sensor.pir": ("binary_sensor", "人体感应"),
    "sensor.doorwindow": ("binary_sensor", "门磁"),
    "sensor.smoke": ("binary_sensor", "烟雾"),
    "sensor.gas": ("binary_sensor", "燃气"),
    "sensor.water": ("binary_sensor", "水浸"),
    "sensor.help": ("binary_sensor", "紧急求助"),
    "sensor.dryContact": ("binary_sensor", "干接点"),
    # 窗帘
    "curtain.shades": ("cover", "百叶帘"),
    "curtain.roller": ("cover", "卷帘"),
    "curtain.trietex": ("cover", "开合帘"),
    "curtain.switch": ("cover", "开关停窗帘"),
}

# 缺少上表条目时按前缀兜底
SPK_PREFIX_PLATFORM: Final[dict[str, str]] = {
    "light.": "light",
    "panel.": "switch",
    "other.": "switch",
    "sensor.": "sensor",
    "curtain.": "cover",
    "hvac.": "climate",
}

PLATFORM_TITLES: Final[dict[str, str]] = {
    "light": "照明",
    "switch": "开关 / 插座",
    "sensor": "数值传感器",
    "binary_sensor": "二元传感器",
    "cover": "窗帘",
    "button": "场景",
}

# ------------------------------------------------------------------ 属性键
KEY_ON_OFF: Final = "on_off"
KEY_DELAY: Final = "delay"
KEY_CURRENT: Final = "current"
KEY_BRIGHTNESS: Final = "brightness"
KEY_COLOR_TEMP: Final = "color_temp"
KEY_POSITION: Final = "position"
KEY_PERCENT: Final = "percent"

# 各 spk 的状态键 -> 含义
SENSOR_KEYS: Final[dict[str, tuple[str, str, str | None]]] = {
    # spk: (属性键候选, HA device_class, 单位)
    "sensor.temperature": ("temperature", "temperature", "°C"),
    "sensor.humidity": ("humidity", "humidity", "%"),
    "sensor.light": ("illuminance", "illuminance", "lx"),
    "sensor.co2": ("co2", "carbon_dioxide", "ppm"),
    "sensor.pm25": ("pm25", "pm25", "µg/m³"),
    "sensor.tvoc": ("tvoc", None, "µg/m³"),
}

BINARY_SENSOR_KEYS: Final[dict[str, tuple[str, str]]] = {
    # spk: (属性键候选, HA device_class)
    "sensor.pir": ("people_status", "motion"),
    "sensor.doorwindow": ("door_status", "door"),
    "sensor.smoke": ("smoke_status", "smoke"),
    "sensor.gas": ("gas_status", "gas"),
    "sensor.water": ("water_status", "moisture"),
    "sensor.help": ("help_status", "problem"),
    "sensor.dryContact": ("contact_status", None),
}

LIGHT_DIMMING_SPKS: Final = ("light.dimming", "light.cct", "light.rgb", "light.rgbw")

# 系统 / 诊断类集成：它们产出的实体不是用户想接进 HDL 的真实设备
SYSTEM_INTEGRATIONS: Final[frozenset[str]] = frozenset(
    {
        "sun",
        "backup",
        "hassio",
        "supervisor",
        "analytics",
        "energy",
        "recorder",
        "history",
        "logbook",
        "updater",
        "update",
        "met",
        "cloud",
        "systemmonitor",
        "mobile_app",
        "repairs",
        "trace",
        "homeassistant",
        "hacs",
        "tailscale",
    }
)


ON_VALUES: Final = ("on", "true", "1", "open", "active")
READ_BATCH: Final = 40        # 单次 property/read 携带的 sid 上限
CONNECT_TIMEOUT: Final = 12
SCAN_CONCURRENCY: Final = 64  # 网段扫描并发
SCAN_TIMEOUT: Final = 2.0     # 单机探测超时（跨 NAT / 跨网段时需要更宽裕）

