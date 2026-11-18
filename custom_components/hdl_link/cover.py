"""窗帘实体（百叶 / 卷帘 / 开合帘）：按网关上送的位置百分比控制。

说明：该类型的位置属性键在 HDL LINK 文档中未固定给出，
集成按 position / percent 两个键自适应读取；读不到值时不报位置，只提供开/关/停。
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_ON_OFF, KEY_PERCENT, KEY_POSITION
from .coordinator import HdlCoordinator
from .entity import HdlEntity, numeric

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HdlCover(coordinator, config)
        for config in coordinator.entities.values()
        if config.get("platform") == "cover"
    ]
    async_add_entities(entities)


class HdlCover(HdlEntity, CoverEntity):
    """HDL 窗帘。"""

    _attr_has_entity_name = True
    _attr_device_class = CoverDeviceClass.CURTAIN

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any]) -> None:
        super().__init__(coordinator, config, "cover")
        self._attr_name = self.func_name
        self._attr_supported_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
        )

    @property
    def current_cover_position(self) -> int | None:
        raw = numeric(self.value(KEY_POSITION, KEY_PERCENT))
        if raw is None:
            return None
        return max(0, min(100, round(raw)))

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is not None:
            return position == 0
        on_off = self.on_state(KEY_ON_OFF)
        return None if on_off is None else not on_off

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_POSITION: "100"})

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_POSITION: "0"})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.async_write(**{KEY_POSITION: str(int(kwargs[ATTR_POSITION]))})
