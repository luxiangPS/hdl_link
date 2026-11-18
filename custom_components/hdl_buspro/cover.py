"""窗帘 → cover（0xE3E2 脉冲式：开/关/停，无位置回读）。"""

from __future__ import annotations

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_data import merged_config
from .const import CONF_COVERS, DOMAIN
from .entity import HdlEntity, device_type_of, parse_address
from .protocol import CMD_CURTAIN, build_curtain_command

ACTION_STOP = 0
ACTION_OPEN = 1
ACTION_CLOSE = 2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    entities: list[HdlCover] = []
    for key in cfg.get(CONF_COVERS, []):
        address_text, channel_text = str(key).split("/")
        entities.append(HdlCover(runtime, entry, parse_address(address_text), int(channel_text), device_type_of(cfg, address_text)))
    async_add_entities(entities)


class HdlCover(HdlEntity):
    """脉冲式窗帘：只上报「正在运动」状态，位置由用户设定的行程时间决定。"""

    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _attr_assumed_state = True

    def __init__(self, runtime, entry, address, channel: int, device_type: int = 0) -> None:
        super().__init__(runtime, entry, address, device_type, key=f"ch{channel}-cover")
        self.channel = channel
        self._attr_name = f"通道{channel}（窗帘）"
        self._moving = False

    @property
    def is_closed(self) -> bool | None:
        # 无位置回读，返回 None 表示状态未知（HA 会显示为「未知」而不是猜测）
        return None

    @property
    def is_opening(self) -> bool:
        return self._moving

    @property
    def is_closing(self) -> bool:
        return False

    async def _send(self, action: int) -> None:
        self._moving = action != ACTION_STOP
        await self.runtime.gateway.async_send(
            CMD_CURTAIN, self.address, build_curtain_command(self.channel, action)
        )
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs) -> None:
        await self._send(ACTION_OPEN)

    async def async_close_cover(self, **kwargs) -> None:
        await self._send(ACTION_CLOSE)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._send(ACTION_STOP)
