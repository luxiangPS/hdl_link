"""数值传感器实体：温度 / 湿度 / 照度 / CO2 / PM2.5 / TVOC。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_KEYS
from .coordinator import HdlCoordinator
from .entity import HdlEntity, numeric

PARALLEL_UPDATES = 0

# 单位用普通字符串，避免不同 HA 版本常量位置变化导致导入失败
DEVICE_CLASS_MAP = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "humidity": SensorDeviceClass.HUMIDITY,
    "illuminance": SensorDeviceClass.ILLUMINANCE,
    "carbon_dioxide": SensorDeviceClass.CO2,
}

TYPE_LABEL = {
    "temperature": "温度",
    "humidity": "湿度",
    "illuminance": "照度",
    "carbon_dioxide": "CO2",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HdlCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        HdlSensor(coordinator, config)
        for config in coordinator.entities.values()
        if config.get("platform") == "sensor" and str(config.get("spk")) in SENSOR_KEYS
    ]
    async_add_entities(entities)


class HdlSensor(HdlEntity, SensorEntity):
    """HDL 数值传感器。"""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any]) -> None:
        super().__init__(coordinator, config, "sensor")
        spk = str(config.get("spk"))
        key, device_class, unit = SENSOR_KEYS[spk]
        self._key = key
        raw = str(config.get("name") or "").strip()
        if raw:
            self._attr_name = f"{self.func_name} {key}" if unit is None else self.func_name
        else:
            self._attr_name = TYPE_LABEL.get(device_class, self.func_name)
        if device_class in DEVICE_CLASS_MAP:
            self._attr_device_class = DEVICE_CLASS_MAP[device_class]
        if unit:
            self._attr_native_unit_of_measurement = unit
        # 湿度与照度用 0 位小数，温度与 CO2 保留精度
        self._attr_suggested_display_precision = 1 if spk == "sensor.temperature" else 0

    @property
    def native_value(self) -> float | None:
        return numeric(self.value(self._key))

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
