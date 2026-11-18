"""HDL LINK 集成入口：一个配置条目 = 一个 HDL 主网关。"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_GW_ID, DOMAIN, MANUFACTURER
from .coordinator import HdlCoordinator
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

HEARTBEAT_INTERVAL = 30


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立 LINK 连接并加载平台。"""
    coordinator = HdlCoordinator(hass, entry)

    try:
        await coordinator.async_connect()
    except (LinkError, OSError) as err:
        LOGGER.error("连接 HDL 网关 %s 失败: %s", entry.data.get("host"), err)
        raise

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # 网关自身作为一个 HA 设备（其余实体通过 via_device 挂到它下面）
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.gw_id)},
        manufacturer=MANUFACTURER,
        model="HDL 主网关（LINK）",
        name=coordinator.gw_name,
        configuration_url=f"http://{coordinator.gateway.host}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await coordinator.gateway.async_heartbeat()

    entry.async_create_background_task(hass, _heartbeat_loop(), f"{DOMAIN}-heartbeat")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载：停平台、关连接。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: HdlCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_close()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = ["async_setup_entry", "async_unload_entry", "CONF_GW_ID"]
