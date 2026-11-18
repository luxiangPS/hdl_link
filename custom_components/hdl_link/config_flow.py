"""配置流程（v2）：自动发现网关 → 自动扫描总线 → 勾选设备与场景 → 生成实体。

设计原则（面向现场安装）：
  * 不要求用户知道网关 IP：先广播/网段发现，列出候选网关供选择；
  * 不要求用户知道总线子网号：自动尝试常见子网（1 与 10）；
  * 不要求用户逐路点名通道：扫描给出的通道数 / 温湿照 / 干接点直接列为"功能属性"，
    用户只勾选要接入的设备，再按需要指定调光、窗帘、门铃；
  * 场景：0x000C 读到的场景号直接列出来勾选，读不到则允许手填号段。
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
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
    CONF_SCENES,
    CONF_SCENE_SUBNET,
    CONF_SUBNET,
    DEFAULT_PORT,
    DOMAIN,
    NAME,
)
from .discovery import (
    DiscoveredDevice,
    GatewayCandidate,
    async_discover_gateways,
    async_read_scenes,
    async_scan_auto,
)
from .gateway import HdlGateway, HdlGatewayError

LOGGER = logging.getLogger(__name__)

STEP_DISCOVERY = "user"
STEP_MANUAL = "manual"
STEP_DEVICES = "devices"
STEP_MAPPING = "mapping"

MANUAL_VALUE = "__manual__"


class HdlLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """一个网关 = 一个配置条目。"""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._subnet: int = 1
        self._found: dict[int, DiscoveredDevice] = {}
        self._scenes: dict[int, int] = {}
        self._candidates: list[GatewayCandidate] = []

    # ---------------- 第 1 步：自动发现网关 ----------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            choice = str(user_input.get(CONF_HOST, "")).strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            if choice == MANUAL_VALUE:
                return await self.async_step_manual()
            self._host = choice
            self._port = port
            return await self._async_probe_and_scan(errors) or await self.async_step_devices()

        # 首次进入：跑一次发现（广播 + 网段兜底，约 8 秒）
        self._candidates = await async_discover_gateways()

        options: list[dict[str, str]] = [
            {"value": item.host, "label": item.describe()} for item in self._candidates
        ]
        options.append({"value": MANUAL_VALUE, "label": "手动输入网关 IP"})

        if not self._candidates:
            errors["base"] = "no_gateway"

        return self.async_show_form(
            step_id=STEP_DISCOVERY,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                }
            ),
            errors=errors,
            description_placeholders={
                "name": NAME,
                "count": str(len(self._candidates)),
            },
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """广播被防火墙挡住 / 跨网段时的手工入口。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = str(user_input[CONF_HOST]).strip()
            self._port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            result = await self._async_probe_and_scan(errors)
            if result is not None:
                return result
            return await self.async_step_devices()

        return self.async_show_form(
            step_id=STEP_MANUAL,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                }
            ),
            errors=errors,
            description_placeholders={"name": NAME},
        )

    async def _async_probe_and_scan(self, errors: dict[str, str]) -> FlowResult | None:
        """连上网关 → 自动判定子网 → 扫描设备与场景。出错时返回错误表单。"""
        self._async_abort_entries_match({CONF_HOST: self._host, CONF_PORT: self._port})

        gateway = HdlGateway(self._host, self._port)
        try:
            await gateway.async_start()
        except OSError as err:
            LOGGER.warning("绑定 UDP %s 失败：%s", self._port, err)
            errors["base"] = "port_in_use"
            return await self.async_step_manual()

        try:
            subnet, found = await async_scan_auto(gateway)
            if not found:
                errors["base"] = "no_devices"
                return await self.async_step_manual()
            self._subnet = subnet
            self._found = found
            self._scenes = await async_read_scenes(gateway, subnet)
            await self.async_set_unique_id(f"{self._host}:{self._port}")
        except HdlGatewayError as err:
            LOGGER.warning("扫描 HDL 网关失败：%s", err)
            errors["base"] = "cannot_connect"
            return await self.async_step_manual()
        finally:
            gateway.close()
        return None

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
                    description_placeholders=self._placeholders(),
                )
            self._found = {
                key: value for key, value in self._found.items()
                if value.address in selected
            }
            return await self.async_step_mapping()

        return self.async_show_form(
            step_id=STEP_DEVICES,
            data_schema=self._devices_schema(options),
            description_placeholders=self._placeholders(),
        )

    def _placeholders(self) -> dict[str, str]:
        channels = ", ".join(
            f"{d.address}/1…{d.channels}" for d in self._found.values() if d.channels
        ) or "无"
        summary = "；".join(d.describe() for d in self._found.values()) or "无"
        return {
            "name": NAME,
            "host": self._host,
            "subnet": str(self._subnet),
            "count": str(len(self._found)),
            "channel_hint": channels,
            "summary": summary,
        }

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

    # ---------------- 第 3 步：功能编排 + 场景 ----------------

    async def async_step_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            dimmers = _split(user_input.get(CONF_DIMMERS, ""))
            covers = _split(user_input.get(CONF_COVERS, ""))
            doorbells = _split(user_input.get(CONF_DOORBELLS, ""))
            scenes = [int(x) for x in _split(str(user_input.get(CONF_SCENES, "")))]
            config: dict[str, Any] = {
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_SUBNET: self._subnet,
                CONF_DEVICES: [d.address for d in self._found.values()],
                CONF_DEVICE_TYPES: {d.address: d.kind for d in self._found.values()},
                CONF_DEVICE_TYPE_NUM: {d.address: d.device_type for d in self._found.values()},
                CONF_CHANNELS: {d.address: d.channels for d in self._found.values() if d.channels},
                CONF_DIMMERS: dimmers,
                CONF_COVERS: covers,
                CONF_DOORBELLS: doorbells,
                CONF_SCENES: scenes,
                CONF_SCENE_SUBNET: self._subnet,
            }
            return self.async_create_entry(title=f"{NAME} {self._host}", data=config)

        scene_hint = "、".join(str(s) for s in sorted(self._scenes)) or "未读到（可手填号段）"
        channel_hint = "，".join(
            f"{d.address} 共 {d.channels} 路" for d in self._found.values() if d.channels
        ) or "无通道设备"

        return self.async_show_form(
            step_id=STEP_MAPPING,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DIMMERS, default=""): TextSelector(),
                    vol.Optional(CONF_COVERS, default=""): TextSelector(),
                    vol.Optional(CONF_DOORBELLS, default=""): TextSelector(),
                    vol.Optional(CONF_SCENES, default=""): TextSelector(),
                }
            ),
            description_placeholders={
                "channel_hint": channel_hint,
                "scene_hint": scene_hint,
            },
        )

    # ---------------- 选项：随时重配 ----------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return HdlLinkOptionsFlow(config_entry)


class HdlLinkOptionsFlow(OptionsFlow):
    """只调功能编排（调光/窗帘/门铃/场景），不动网关连接。"""

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
                    CONF_SCENES: [int(x) for x in _split(str(user_input.get(CONF_SCENES, "")))],
                },
            )
        data = self._entry.options or self._entry.data
        subdivided = ", ".join(str(s) for s in data.get(CONF_SCENES, []))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DIMMERS, default=", ".join(data.get(CONF_DIMMERS, []))
                    ): TextSelector(),
                    vol.Optional(
                        CONF_COVERS, default=", ".join(data.get(CONF_COVERS, []))
                    ): TextSelector(),
                    vol.Optional(
                        CONF_DOORBELLS, default=", ".join(data.get(CONF_DOORBELLS, []))
                    ): TextSelector(),
                    vol.Optional(CONF_SCENES, default=subdivided): TextSelector(),
                }
            ),
        )


def _split(text: str) -> list[str]:
    """'10.100/9, 10.100/10' → ['10.100/9', '10.100/10']。"""
    return [part.strip() for part in str(text).replace("，", ",").split(",") if part.strip()]
