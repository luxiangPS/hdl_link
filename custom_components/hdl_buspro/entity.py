"""实体基类：统一设备信息、地址解析与更新订阅。"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_DEVICE_TYPE_NUM, DOMAIN, MANUFACTURER, SIGNAL_UPDATE
from .protocol import DEV_TYPE_NAMES


def device_type_of(cfg, address_text: str) -> int:
    """从配置里取该设备的 HDL 类型号，取不到返回 0。"""
    return int((cfg.get(CONF_DEVICE_TYPE_NUM) or {}).get(str(address_text), 0))


def parse_address(text: str) -> tuple[int, int]:
    """'10.100' -> (10, 100)。"""
    radio, device = text.split(".")
    return int(radio), int(device)


class HdlEntity(Entity):
    """所有 HDL 实体的公共部分。"""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime,
        entry: ConfigEntry,
        address: tuple[int, int],
        device_type: int,
        *,
        key: str,
    ) -> None:
        self.runtime = runtime
        self.entry = entry
        self.address = address
        self._device_type = device_type
        self._attr_unique_id = f"{entry.entry_id}-{address[0]}.{address[1]}-{key}"

    @property
    def device_info(self) -> DeviceInfo:
        address_text = f"{self.address[0]}.{self.address[1]}"
        device_type = self._device_type
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}-{address_text}")},
            manufacturer=MANUFACTURER,
            model=DEV_TYPE_NAMES.get(device_type, f"HDL 总线设备 {device_type}"),
            name=f"HDL {address_text}",
            suggested_area=None,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATE}_{self.address[0]}.{self.address[1]}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self, frame=None) -> None:
        self.async_write_ha_state()
