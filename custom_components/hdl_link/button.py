"""场景按钮：把 HDL LINK 的场景号暴露成 HA 的 button，按下即发送 0xE01C。"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_data import merged_config
from .const import CONF_SCENES, CONF_SCENE_SUBNET, DOMAIN
from .entity import HdlEntity

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """按配置里的场景号建按钮。"""
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    scenes = [int(s) for s in cfg.get(CONF_SCENES, []) if str(s).strip()]
    subnet = int(cfg.get(CONF_SCENE_SUBNET, 1))

    entities = [HdlSceneButton(runtime, entry, scene, subnet) for scene in scenes]
    if entities:
        async_add_entities(entities)


class HdlSceneButton(HdlEntity, ButtonEntity):
    """一个 HDL 场景 = 一个按钮实体。"""

    _attr_icon = "mdi:palette"

    def __init__(self, runtime, entry: ConfigEntry, scene: int, subnet: int) -> None:
        super().__init__(runtime, entry, (subnet, 254), 0, key=f"scene-{scene}")
        self._scene = scene
        self._attr_name = f"场景 {scene}"

    async def async_press(self) -> None:
        """发送场景唤醒。"""
        LOGGER.debug("触发 HDL 场景 %s（子网 %s）", self._scene, self.address[0])
        await self.runtime.async_run_scene(self._scene, self.address[0])
