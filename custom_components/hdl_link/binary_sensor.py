"""二元传感器实体：PIR / 门磁 / 烟雾 / 燃气 / 水浸 / 求助 / 干接点。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BINARY_SENSOR_KEYS, DOMAIN, ON_VALUES
from .coordinator import HdlCoordinator
from .entity import HdlEntity

PARALLEL_UPDATES = 0

DEVICE_CLASS_MAP = {
    "motion": BinarySensorDeviceClass.MOTION,
    "door": BinarySensorDeviceClass.DOOR,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "gas": BinarySensorDeviceClass.GAS,
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "problem": BinarySensorDeviceClass.PROBLEM,
}

TYPE_LABEL = {
    "motion": "人体感应",
    "door": "门磁",
    "smoke": "烟感",
    "gas": "燃气",
    "moisture": "水浸",
    "problem": "告警",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HdlBinarySensor(coordinator, config)
        for config in coordinator.entities.values()
        if config.get("platform") == "binary_sensor" and str(config.get("spk")) in BINARY_SENSOR_KEYS
    ]
    async_add_entities(entities)


class HdlBinarySensor(HdlEntity, BinarySensorEntity):
    """HDL 开关量传感器。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any]) -> None:
        super().__init__(coordinator, config, "binary_sensor")
        spk = str(config.get("spk"))
        key, device_class = BINARY_SENSOR_KEYS[spk]
        self._key = key
        # 工程里没起名、或名字与设备名重复时用类型名（人体感应 / 门磁 …）
        raw = str(config.get("name") or "").strip()
        dev_name = str((coordinator.devices.get(self.oid, {}) or {}).get("device_name") or "").strip()
        if raw and raw != dev_name:
            self._attr_name = raw
        else:
            self._attr_name = TYPE_LABEL.get(device_class, self.func_name)
        if device_class in DEVICE_CLASS_MAP:
            self._attr_device_class = DEVICE_CLASS_MAP[device_class]

    @property
    def is_on(self) -> bool | None:
        raw = self.value(self._key)
        if raw is None:
            return None
        return str(raw).strip().lower() in ON_VALUES

    @property
    def available(self) -> bool:
        return super().available and self.value(self._key) is not None
