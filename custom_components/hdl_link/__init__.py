"""HDL LINK 集成入口：一条总线一个网关连接，轮询通道状态 + 推送传感器。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .config_data import merged_config
from .const import (
    CONF_CHANNELS,
    CONF_DEVICES,
    CONF_DEVICE_TYPES,
    CONF_PORT,
    DEVICE_KIND_RELAY,
    DEVICE_KIND_SENSOR,
    DOMAIN,
    POLL_INTERVAL,
    SENSOR_POLL_INTERVAL,
    SIGNAL_UPDATE,
)
from .gateway import HdlGateway, HdlGatewayError
from .protocol import (
    CMD_SCENE_CONTROL,
    SENDER_DEVICE,
    SENDER_SUBNET,
    SENDER_TYPE,
    build_scene_command,
)

LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass(slots=True)
class HdlRuntime:
    """一次配置条目的运行时状态。"""

    gateway: HdlGateway
    relays: list[tuple[int, int]] = field(default_factory=list)
    sensors: list[tuple[int, int]] = field(default_factory=list)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def channel_level(self, address: tuple[int, int], channel: int) -> int:
        return self.gateway.channel_states.get(address, {}).get(channel, 0)

    def sensor_value(self, address: tuple[int, int], key: str) -> float | None:
        return self.gateway.sensor_states.get(address, {}).get(key)

    async def async_poll_relays(self, hass: HomeAssistant) -> None:
        for address in self.relays:
            result = await self.gateway.async_poll_channels(address)
            if result is not None:
                async_dispatcher_send(hass, f"{SIGNAL_UPDATE}_{address[0]}.{address[1]}")

    async def async_poll_sensors(self, hass: HomeAssistant) -> None:
        for address in self.sensors:
            result = await self.gateway.async_read_sensor(address)
            if result is not None:
                async_dispatcher_send(hass, f"{SIGNAL_UPDATE}_{address[0]}.{address[1]}")

    async def async_run_scene(self, scene: int, subnet: int) -> None:
        """触发 HDL 场景（0xE01C）。"""
        await self.gateway.async_send(
            CMD_SCENE_CONTROL, (subnet, 254), build_scene_command(scene)
        )

    def stop_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def start_tasks(self, hass: HomeAssistant) -> None:
        async def _relay_loop() -> None:
            while True:
                await self.async_poll_relays(hass)
                await asyncio.sleep(POLL_INTERVAL)

        async def _sensor_loop() -> None:
            while True:
                await self.async_poll_sensors(hass)
                await asyncio.sleep(SENSOR_POLL_INTERVAL)

        if self.relays:
            self._tasks.append(hass.async_create_background_task(_relay_loop(), f"{DOMAIN}-relay-poll"))
        if self.sensors:
            self._tasks.append(
                hass.async_create_background_task(_sensor_loop(), f"{DOMAIN}-sensor-poll")
            )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立网关连接并加载平台。"""
    cfg = merged_config(entry)
    host = cfg[CONF_HOST]
    port = int(cfg.get(CONF_PORT, 6000))
    gateway = HdlGateway(host, port, source_ip=cfg.get("source_ip"))

    try:
        await gateway.async_start()
    except OSError as err:
        LOGGER.error("绑定 UDP %s 失败（多为此端口已被 HDL 调试工具或旧桥接占用）: %s", port, err)
        raise

    runtime = HdlRuntime(gateway=gateway)
    for address in cfg.get(CONF_DEVICES, []):
        subnet_text, device_text = str(address).split(".")
        target = (int(subnet_text), int(device_text))
        kind = cfg.get(CONF_DEVICE_TYPES, {}).get(str(address), DEVICE_KIND_RELAY)
        if kind == DEVICE_KIND_SENSOR:
            runtime.sensors.append(target)
        else:
            runtime.relays.append(target)

    def _on_event(frame: Any) -> None:
        """网关主动上报（干接点等）转发给实体。"""
        async_dispatcher_send(
            hass, f"{SIGNAL_UPDATE}_{frame.sender[0]}.{frame.sender[1]}", frame
        )

    gateway.add_listener(_on_event)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    runtime.start_tasks(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载：停轮询、关 socket。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: HdlRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        runtime.stop_tasks()
        runtime.gateway.close()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


__all__ = ["async_setup_entry", "async_unload_entry", "async_reload_entry", "HdlRuntime",
           "SENDER_SUBNET", "SENDER_DEVICE", "SENDER_TYPE", "CONF_CHANNELS", "HdlGatewayError"]

# 平台模块预热：HA 2025.7+ 会把「事件循环里 import 自定义集成平台模块」判为阻塞调用错误，
# 使 async_forward_entry_setups 失败（表现为集成「设置失败」）。__init__ 本身是 HA 在
# executor 中加载的，在这里预导入各平台后，后续 forward 直接命中 sys.modules，不再阻塞。
from . import binary_sensor, button, cover, light, sensor, switch  # noqa: E402,F401
