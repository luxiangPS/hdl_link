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
CONF_LOCAL_SECRET: Final = "local_secret"   # 网关 /hdlFamily/config/localSecret.sys（可选，开启实时上报）

DEFAULT_PORT: Final = 8586
DEFAULT_POLL_INTERVAL: Final = 10      # 秒，属性轮询周期
MIN_POLL_INTERVAL: Final = 3
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

ON_VALUES: Final = ("on", "true", "1", "open", "active")
READ_BATCH: Final = 40        # 单次 property/read 携带的 sid 上限
CONNECT_TIMEOUT: Final = 12
SCAN_CONCURRENCY: Final = 60  # 网段扫描并发
SCAN_TIMEOUT: Final = 0.9
