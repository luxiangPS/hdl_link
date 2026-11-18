"""调光通道 → light（用 0x0031 的 level 0-100 表示亮度）。"""

from __future__ import annotations

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_data import merged_config
from .const import CONF_DIMMERS, DOMAIN, POLL_INTERVAL
from .entity import HdlEntity, device_type_of, parse_address


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    entities: list[HdlLight] = []
    for key in cfg.get(CONF_DIMMERS, []):
        address_text, channel_text = str(key).split("/")
        entities.append(
            HdlLight(runtime, entry, parse_address(address_text), int(channel_text), device_type_of(cfg, address_text))
        )
    async_add_entities(entities)


class HdlLight(HdlEntity):
    """可调光通道。HDL 亮度 0-100 线性映射到 HA 0-255。"""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, runtime, entry, address, channel: int, device_type: int = 0) -> None:
        super().__init__(runtime, entry, address, device_type, key=f"ch{channel}-light")
        self.channel = channel
        self._attr_name = f"通道{channel}（调光）"

    @property
    def _level(self) -> int:
        return self.runtime.channel_level(self.address, self.channel)

    @property
    def is_on(self) -> bool:
        return self._level > 0

    @property
    def brightness(self) -> int | None:
        level = self._level
        return None if level <= 0 else round(level * 255 / 100)

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = _to_level(kwargs[ATTR_BRIGHTNESS])
        else:
            level = self._level or 100
        await self.runtime.gateway.async_set_channel(self.address, self.channel, level)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.gateway.async_set_channel(self.address, self.channel, 0)
        self.async_write_ha_state()


def _to_level(brightness: int) -> int:
    """HA 亮度 0-255 → HDL level 1-100。"""
    return max(1, min(100, round(brightness * 100 / 255)))
