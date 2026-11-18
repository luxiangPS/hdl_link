"""开关 / 插座实体：other.common、panel.socket 等 relay 型功能。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_ON_OFF
from .coordinator import HdlCoordinator
from .entity import HdlEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HdlSwitch(coordinator, config)
        for config in coordinator.entities.values()
        if config.get("platform") == "switch"
    ]
    async_add_entities(entities)


class HdlSwitch(HdlEntity, SwitchEntity):
    """继电器触点 / 插座：开 / 关。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any]) -> None:
        super().__init__(coordinator, config, "switch")
        self._attr_name = self.func_name

    @property
    def is_on(self) -> bool | None:
        return self.is_on(KEY_ON_OFF)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_ON_OFF: "on"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_ON_OFF: "off"})
