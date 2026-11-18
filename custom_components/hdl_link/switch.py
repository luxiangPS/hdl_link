"""继电器通道 → switch。"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_data import merged_config
from .const import CONF_CHANNELS, CONF_COVERS, CONF_DEVICES, CONF_DIMMERS, CONF_DEVICE_TYPES, DOMAIN, DEVICE_KIND_RELAY
from .entity import HdlEntity, device_type_of, parse_address

LOGGER = logging.getLogger(__name__)

ON_LEVEL = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    dimmers = set(cfg.get(CONF_DIMMERS, []))
    covers = set(cfg.get(CONF_COVERS, []))
    channels_map: dict[str, int] = cfg.get(CONF_CHANNELS, {})
    kinds: dict[str, str] = cfg.get(CONF_DEVICE_TYPES, {})

    entities: list[HdlSwitch] = []
    for address_text in cfg.get(CONF_DEVICES, []):
        if kinds.get(str(address_text)) != DEVICE_KIND_RELAY:
            continue
        count = int(channels_map.get(str(address_text), 0))
        address = parse_address(str(address_text))
        for channel in range(1, count + 1):
            key = f"{address_text}/{channel}"
            if key in dimmers or key in covers:
                continue  # 由 light.py / cover.py 负责
            entities.append(HdlSwitch(runtime, entry, address, channel, device_type_of(cfg, str(address_text))))

    async_add_entities(entities)


class HdlSwitch(HdlEntity):
    """单通道开关。"""

    _attr_assumed_state = False

    def __init__(self, runtime, entry, address, channel: int, device_type: int) -> None:
        super().__init__(runtime, entry, address, device_type, key=f"ch{channel}")
        self.channel = channel
        self._attr_name = f"通道{channel}"

    @property
    def is_on(self) -> bool:
        return self.runtime.channel_level(self.address, self.channel) > 0

    async def async_turn_on(self, **kwargs) -> None:
        await self.runtime.gateway.async_set_channel(self.address, self.channel, ON_LEVEL)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.gateway.async_set_channel(self.address, self.channel, 0)
        self.async_write_ha_state()
