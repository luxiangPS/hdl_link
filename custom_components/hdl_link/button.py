"""场景按钮：把网关内 scene/list/get 的场景做成按钮实体。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NAME
from .coordinator import HdlCoordinator
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [HdlSceneButton(coordinator, scene) for scene in coordinator.scenes if scene.get("sid")]
    async_add_entities(entities)


class HdlSceneButton(CoordinatorEntity[HdlCoordinator], ButtonEntity):
    """执行 HDL 网关内的一个场景。"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:palette"

    def __init__(self, coordinator: HdlCoordinator, scene: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self.scene_sid = str(scene["sid"])
        self.scene_name = str(scene.get("name") or self.scene_sid)
        self._attr_unique_id = f"{coordinator.gw_id}-{self.scene_sid}-scene"
        self._attr_name = self.scene_name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.gw_id}-scenes")},
            manufacturer=MANUFACTURER,
            model=f"{NAME} 场景",
            name=f"{self.coordinator.gw_name} 场景",
            via_device=(DOMAIN, self.coordinator.gw_id),
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.gateway.async_execute_scene(self.scene_sid)
        except LinkError as err:
            LOGGER.warning("执行场景 %s 失败: %s", self.scene_name, err)
            return
        await self.coordinator.async_poll_now()
