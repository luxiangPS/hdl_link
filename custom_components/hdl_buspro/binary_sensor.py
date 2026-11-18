"""门铃 / 干接点 → binary_sensor（监听 0x15D0 广播，status=0 表示触发）。"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .config_data import merged_config
from .const import CONF_DOORBELLS, DOORBELL_EVENT_TIMEOUT, DOMAIN
from .entity import HdlEntity, device_type_of, parse_address
from .protocol import CMD_DRY_CONTACT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    cfg = merged_config(entry)
    entities = [
        HdlDoorbell(runtime, entry, parse_address(str(address_text)), device_type_of(cfg, address_text))
        for address_text in cfg.get(CONF_DOORBELLS, [])
    ]
    async_add_entities(entities)


class HdlDoorbell(HdlEntity):
    """干接点事件型传感器：收到广播置 on，DOORBELL_EVENT_TIMEOUT 秒后自动回到 off。"""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:doorbell"

    def __init__(self, runtime, entry, address, device_type: int = 0) -> None:
        super().__init__(runtime, entry, address, device_type, key="doorbell")
        self._attr_name = "门铃"
        self._is_on = False
        self._reset_handle = None

    @property
    def is_on(self) -> bool:
        return self._is_on

    @callback
    def _handle_update(self, frame=None) -> None:
        if frame is None or frame.code != CMD_DRY_CONTACT:
            return
        if frame.sender[0:2] != self.address:
            return
        payload = frame.payload
        # payload: area, channel, status；status == 0 表示触发
        if len(payload) < 3 or payload[2] != 0:
            return
        self._is_on = True
        self.async_write_ha_state()
        if self._reset_handle is not None:
            self._reset_handle()
        self._reset_handle = async_call_later(
            self.hass, DOORBELL_EVENT_TIMEOUT, self._async_reset
        )

    @callback
    def _async_reset(self, _now=None) -> None:
        self._is_on = False
        self._reset_handle = None
        self.async_write_ha_state()
