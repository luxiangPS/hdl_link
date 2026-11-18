"""传感器实体：温度 / 照度（来自 0x1605 应答与 0x1630 上报）。"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfIlluminance, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_data import merged_config
from .const import CONF_DEVICES, CONF_DEVICE_TYPES, DEVICE_KIND_SENSOR, DOMAIN
from .entity import HdlEntity, device_type_of, parse_address

SENSOR_FIELDS = (
    {
        "key": "temperature",
        "name": "温度",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "digits": 1,
    },
    {
        "key": "illuminance",
        "name": "照度",
        "device_class": SensorDeviceClass.ILLUMINANCE,
        "unit": UnitOfIlluminance.LUX,
        "state_class": SensorStateClass.MEASUREMENT,
        "digits": 0,
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    kinds = cfg.get(CONF_DEVICE_TYPES, {})
    entities: list[HdlSensor] = []
    for address_text in cfg.get(CONF_DEVICES, []):
        if kinds.get(str(address_text)) != DEVICE_KIND_SENSOR:
            continue
        address = parse_address(str(address_text))
        for field in SENSOR_FIELDS:
            entities.append(HdlSensor(runtime, entry, address, field, device_type_of(cfg, address_text)))
    async_add_entities(entities)


class HdlSensor(HdlEntity):
    """单个测量值。"""

    _attr_has_entity_name = True

    def __init__(self, runtime, entry, address, field: dict, device_type: int = 0) -> None:
        super().__init__(runtime, entry, address, device_type, key=field["key"])
        self._field = field
        self._attr_name = field["name"]
        self._attr_device_class = field["device_class"]
        self._attr_native_unit_of_measurement = field["unit"]
        self._attr_state_class = field["state_class"]
        self._attr_suggested_display_precision = field["digits"]

    @property
    def native_value(self) -> float | None:
        value = self.runtime.sensor_value(self.address, self._field["key"])
        if value is None:
            return None
        return round(value, self._field["digits"])
