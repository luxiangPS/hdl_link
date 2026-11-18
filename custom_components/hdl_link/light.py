"""照明实体：light.switch / dimming / cct / rgb / rgbw。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_CURRENT, KEY_ON_OFF, LIGHT_DIMMING_SPKS
from .coordinator import HdlCoordinator
from .entity import HdlEntity, numeric

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HdlLight(coordinator, config)
        for config in coordinator.entities.values()
        if config.get("platform") == "light"
    ]
    async_add_entities(entities)


class HdlLight(HdlEntity, LightEntity):
    """HDL 照明：开关灯与可调光灯。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any]) -> None:
        super().__init__(coordinator, config, "light")
        self._attr_name = self.func_name
        self._dimmable = self.spk in LIGHT_DIMMING_SPKS
        if self._dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        return self.is_on(KEY_ON_OFF)

    @property
    def brightness(self) -> int | None:
        if not self._dimmable:
            return None
        current = numeric(self.value(KEY_CURRENT))
        if current is None:
            return None
        return max(0, min(255, round(current * 255)))

    async def async_turn_on(self, **kwargs: Any) -> None:
        attrs: dict[str, str] = {KEY_ON_OFF: "on"}
        if self._dimmable and ATTR_BRIGHTNESS in kwargs:
            level = int(kwargs[ATTR_BRIGHTNESS]) / 255.0
            attrs[KEY_CURRENT] = f"{level:.4f}"
        await self.async_write(**attrs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_ON_OFF: "off"})
