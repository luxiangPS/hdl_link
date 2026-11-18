"""配置流程：填网关地址 → 扫描总线 → 勾选设备 → 指定调光/窗帘/门铃通道。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_CHANNELS,
    CONF_COVERS,
    CONF_DEVICES,
    CONF_DEVICE_TYPES,
    CONF_DEVICE_TYPE_NUM,
    CONF_DIMMERS,
    CONF_DOORBELLS,
    CONF_PORT,
    CONF_SUBNET,
    DEFAULT_PORT,
    DEFAULT_SCAN_SUBNET,
    DOMAIN,
    NAME,
)
from .discovery import DiscoveredDevice, async_scan
from .gateway import HdlGateway, HdlGatewayError

LOGGER = logging.getLogger(__name__)

STEP_USER = "user"
STEP_DEVICES = "devices"
STEP_MAPPING = "mapping"


class HdlBusproConfigFlow(ConfigFlow, domain=DOMAIN):
    """单个网关一个配置条目。"""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._subnet: int = DEFAULT_SCAN_SUBNET
        self._found: dict[int, DiscoveredDevice] = {}
        self._selected: list[str] = []

    # ---------------- 第 1 步：网关地址 ----------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            subnet = int(user_input.get(CONF_SUBNET, DEFAULT_SCAN_SUBNET))

            await self.async_set_unique_id(f"{host}:{port}")
            self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

            gateway = HdlGateway(host, port)
            try:
                await gateway.async_start()
            except OSError:
                errors["base"] = "port_in_use"
            else:
                try:
                    self._found = await async_scan(gateway, subnet)
                except HdlGatewayError:
                    errors["base"] = "cannot_connect"
                finally:
                    gateway.close()

                if not errors and not self._found:
                    errors["base"] = "no_devices"

            if not errors:
                self._host = host
                self._port = port
                self._subnet = subnet
                return await self.async_step_devices()

        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_SUBNET, default=DEFAULT_SCAN_SUBNET): int,
                }
            ),
            errors=errors,
            description_placeholders={"name": NAME},
        )

    # ---------------- 第 2 步：勾选设备 ----------------

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = [
            {"value": item.address, "label": item.describe()}
            for item in sorted(self._found.values(), key=lambda d: d.device)
        ]

        if user_input is not None:
            selected = list(user_input.get(CONF_DEVICES, []))
            if not selected:
                return self.async_show_form(
                    step_id=STEP_DEVICES,
                    data_schema=self._devices_schema(options),
                    errors={"base": "no_devices"},
                )
            self._selected = selected
            return await self.async_step_mapping()

        return self.async_show_form(
            step_id=STEP_DEVICES,
            data_schema=self._devices_schema(options),
            description_placeholders={"count": str(len(options))},
        )

    @staticmethod
    def _devices_schema(options: list[dict[str, str]]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_DEVICES): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )

    # ---------------- 第 3 步：通道编排 ----------------

    async def async_step_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            dimmers = _split(user_input.get(CONF_DIMMERS, ""))
            covers = _split(user_input.get(CONF_COVERS, ""))
            doorbells = _split(user_input.get(CONF_DOORBELLS, ""))
            if self._subnet and self._selected:
                config: dict[str, Any] = {
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_SUBNET: self._subnet,
                    CONF_DEVICES: self._selected,
                    CONF_DEVICE_TYPES: {
                        item.address: item.kind
                        for item in self._found.values()
                        if item.address in self._selected
                    },
                    CONF_DEVICE_TYPE_NUM: {
                        item.address: item.device_type
                        for item in self._found.values()
                        if item.address in self._selected
                    },
                    CONF_CHANNELS: {
                        item.address: item.channels
                        for item in self._found.values()
                        if item.address in self._selected and item.channels
                    },
                    CONF_DIMMERS: dimmers,
                    CONF_COVERS: covers,
                    CONF_DOORBELLS: doorbells,
                }
                return self.async_create_entry(
                    title=f"{NAME} {self._host}", data=config
                )

        relay_hint = ", ".join(
            f"{d.address}/{i}" for d in self._found.values() if d.channels for i in (1,)
        )
        return self.async_show_form(
            step_id=STEP_MAPPING,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DIMMERS, default=""): TextSelector(),
                    vol.Optional(CONF_COVERS, default=""): TextSelector(),
                    vol.Optional(CONF_DOORBELLS, default=""): TextSelector(),
                }
            ),
            description_placeholders={"relay_hint": relay_hint},
        )

    # ---------------- 选项：改了随时重配 ----------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return HdlBusproOptionsFlow(config_entry)


class HdlBusproOptionsFlow(OptionsFlow):
    """只调通道编排（调光/窗帘/门铃），不动网关地址。"""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_DIMMERS: _split(user_input.get(CONF_DIMMERS, "")),
                    CONF_COVERS: _split(user_input.get(CONF_COVERS, "")),
                    CONF_DOORBELLS: _split(user_input.get(CONF_DOORBELLS, "")),
                },
            )
        data = self._entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DIMMERS, default=", ".join(data.get(CONF_DIMMERS, []))): TextSelector(),
                    vol.Optional(CONF_COVERS, default=", ".join(data.get(CONF_COVERS, []))): TextSelector(),
                    vol.Optional(CONF_DOORBELLS, default=", ".join(data.get(CONF_DOORBELLS, []))): TextSelector(),
                }
            ),
        )


def _split(text: str) -> list[str]:
    """'10.100/9, 10.100/10' → ['10.100/9', '10.100/10']。"""
    return [part.strip() for part in str(text).replace("，", ",").split(",") if part.strip()]
