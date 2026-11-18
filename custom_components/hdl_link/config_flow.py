"""配置流：广播/网段搜索网关 → 读取网关内功能与场景 → 勾选接入。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENTITIES,
    CONF_GW_ID,
    CONF_GW_NAME,
    CONF_HOST,
    CONF_LOCAL_SECRET,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_SCENES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    NAME,
    PLATFORM_TITLES,
    SPK_PLATFORM,
    SPK_PREFIX_PLATFORM,
)
from .discovery import LAST_SCAN, async_scan, expand_hosts
from .gateway import FunctionInfo, HdlGateway, GatewayInfo
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)

MANUAL_HOST = "__manual__"


def platform_of(spk: str) -> str:
    """SPK → HA 平台。未知类型返回空串（不生成实体）。"""
    if spk in SPK_PLATFORM:
        return SPK_PLATFORM[spk][0]
    for prefix, plat in SPK_PREFIX_PLATFORM.items():
        if spk.startswith(prefix):
            return plat
    return ""


def platform_of_function(func: FunctionInfo) -> str:
    return platform_of(func.spk)


def entity_label(func: FunctionInfo) -> str:
    type_name = SPK_PLATFORM.get(func.spk, (None, func.spk))[1]
    return f"{func.name}（{type_name}）"


async def async_load_gateway(host: str, oid: str = "") -> tuple[GatewayInfo, list[FunctionInfo], list[dict[str, Any]]]:
    """连接网关并读取功能清单与场景列表。"""
    gateway = HdlGateway(host, oid or None, DEFAULT_PORT)
    try:
        info = await gateway.async_connect()
        functions = await gateway.async_functions()
        try:
            scenes = await gateway.async_scenes()
        except LinkError:
            scenes = []
    finally:
        await gateway.async_close()
    return info, functions, scenes


class HdlLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """首次接入。"""

    VERSION = 1

    def __init__(self) -> None:
        self._info: GatewayInfo | None = None
        self._functions: list[FunctionInfo] = []
        self._scenes: list[dict[str, Any]] = []
        self._selected: list[str] = []
        self._local_secret: str = ""
        self._found: list[GatewayInfo] | None = None

    # ---------------------------------------------------------------- 第 1 步
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """搜索网关；搜不到时可直接填网关 IP 或网段（跨网段部署靠这一步）。"""
        errors: dict[str, str] = {}
        manual_value = ""

        if user_input is not None:
            manual_value = str(user_input.get("manual_host") or "").strip()
            host = ""
            picked = str(user_input.get("gateway") or "")
            if picked and picked != MANUAL_HOST:
                # 下拉里明确选了一台发现的网关，优先用它
                host = picked
            elif manual_value:
                hosts, is_range = expand_hosts(manual_value)
                if not hosts:
                    errors["base"] = "invalid_host"
                elif is_range:
                    found = await async_scan(hosts, timeout=30)
                    if found:
                        self._found = found
                        if len(found) == 1:
                            host = found[0].host
                        # 命中多台时先不连，回到表单把结果列进下拉，由用户挑一台
                    else:
                        errors["base"] = "cannot_connect"
                        self._async_report_scan(hosts, LAST_SCAN)
                else:
                    host = hosts[0]
            else:
                errors["base"] = "need_host"

            if not errors and host:
                try:
                    self._info, self._functions, self._scenes = await async_load_gateway(host)
                except LinkError as err:
                    LOGGER.warning("连接 %s 失败: %s", host, err)
                    errors["base"] = "cannot_connect"
                else:
                    self._async_check_duplicate(self._info)
                    if not self._functions:
                        errors["base"] = "no_functions"
                    else:
                        return await self.async_step_entities()

        if self._found is None:
            self._found = await async_scan([])
        found = self._found

        options = [
            selector.SelectOptionDict(
                value=g.host,
                label=f"{g.name} · {g.host}" + (f" · {g.model}" if g.model else ""),
            )
            for g in found
        ]
        options.append(selector.SelectOptionDict(value=MANUAL_HOST, label="手动输入网关 IP / 网段"))

        schema = vol.Schema(
            {
                vol.Required(
                    "gateway",
                    default=options[0]["value"] if found else MANUAL_HOST,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(
                    "manual_host",
                    default="" if len(found) > 1 else manual_value,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"count": str(len(found))},
        )

    # ---------------------------------------------------------------- 手动 IP
    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            try:
                self._info, self._functions, self._scenes = await async_load_gateway(host)
            except LinkError as err:
                LOGGER.warning("连接 %s 失败: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                self._async_check_duplicate(self._info)
                if not self._functions:
                    errors["base"] = "no_functions"
                else:
                    return await self.async_step_entities()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default="192.168.1.150"): str}),
            errors=errors,
        )

    # ---------------------------------------------------------------- 第 2 步
    @callback
    def _async_report_scan(self, hosts: list[str], diag: dict) -> None:
        """扫描失败时把诊断信息发到通知栏，便于现场判断是网络还是扫描问题。"""
        try:
            self.hass.async_create_task(
                persistent_notification.async_create(
                    self.hass,
                    "目标地址 {} 个\nUDP 本地搜索：命中 {}，参数 {}\nTCP 探测：{}".format(
                        len(hosts),
                        diag.get("udp", 0),
                        diag.get("udp_last", {}),
                        diag.get("tcp", {}),
                    ),
                    title="HDL LINK 扫描诊断",
                    notification_id="hdl_link_scan_diag",
                )
            )
        except Exception:  # noqa: BLE001 - 诊断失败不影响主流程
            LOGGER.debug("写入扫描诊断通知失败", exc_info=True)

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """勾选要接入的功能（来自网关内 function/list/get 的清单）。"""
        assert self._info is not None
        supported = [f for f in self._functions if platform_of_function(f)]
        unsupported = len(self._functions) - len(supported)

        if user_input is not None:
            picked = [sid for sid in user_input.get("entities", []) if sid]
            if not picked:
                return self.async_show_form(
                    step_id="entities",
                    data_schema=self._entities_schema(supported),
                    errors={"base": "no_selection"},
                    description_placeholders={
                        "host": self._info.host,
                        "oid": self._info.oid,
                        "total": str(len(self._functions)),
                        "supported": str(len(supported)),
                        "unsupported": str(unsupported),
                    },
                )
            self._selected = picked
            return await self.async_step_secret()

        return self.async_show_form(
            step_id="entities",
            data_schema=self._entities_schema(supported),
            description_placeholders={
                "host": self._info.host,
                "oid": self._info.oid,
                "total": str(len(self._functions)),
                "supported": str(len(supported)),
                "unsupported": str(unsupported),
            },
        )

    def _entities_schema(self, supported: list[FunctionInfo]) -> vol.Schema:
        options = []
        for func in sorted(supported, key=lambda f: (platform_of_function(f), f.name)):
            plat = platform_of_function(func)
            options.append(
                selector.SelectOptionDict(
                    value=func.sid, label=f"[{PLATFORM_TITLES.get(plat, plat)}] {entity_label(func)}"
                )
            )
        return vol.Schema(
            {
                vol.Required("entities", default=[o["value"] for o in options]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    # ---------------------------------------------------------------- 第 3 步
    async def async_step_secret(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """可选的本地加密密钥：填了才解析网关主动上报（实时状态），不填则纯轮询。"""
        if user_input is not None:
            self._local_secret = str(user_input.get(CONF_LOCAL_SECRET) or "").strip()
            return await self.async_step_scenes()
        return self.async_show_form(
            step_id="secret",
            data_schema=vol.Schema({vol.Optional(CONF_LOCAL_SECRET, default=""): str}),
            errors={},
        )

    # ---------------------------------------------------------------- 第 4 步
    async def async_step_scenes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """勾选网关内的场景（每个场景生成一个按钮实体）。"""
        if user_input is not None:
            return self.async_create_entry(
                title=self._title(),
                data=self._entry_data(user_input.get("scenes", [])),
            )

        options = [
            selector.SelectOptionDict(
                value=str(s.get("sid")),
                label=f"{s.get('name') or s.get('sid')}（场景）",
            )
            for s in self._scenes
            if s.get("sid")
        ]
        if not options:
            return self.async_create_entry(title=self._title(), data=self._entry_data([]))

        return self.async_show_form(
            step_id="scenes",
            data_schema=vol.Schema(
                {
                    vol.Optional("scenes", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(options))},
        )

    # ---------------------------------------------------------------- 组装
    def _title(self) -> str:
        assert self._info is not None
        return f"{NAME} {self._info.name} ({self._info.host})"

    def _entry_data(self, scene_sids: list[str]) -> dict[str, Any]:
        assert self._info is not None
        by_sid = {f.sid: f for f in self._functions}
        entities = []
        for sid in self._selected:
            func = by_sid.get(sid)
            if func is None:
                continue
            entities.append(
                {
                    "sid": func.sid,
                    "spk": func.spk,
                    "oid": func.oid,
                    "name": func.name,
                    "platform": platform_of_function(func),
                }
            )
        scenes = [
            {"sid": str(s.get("sid")), "name": str(s.get("name") or s.get("sid"))}
            for s in self._scenes
            if str(s.get("sid")) in scene_sids
        ]
        return {
            CONF_HOST: self._info.host,
            CONF_PORT: DEFAULT_PORT,
            CONF_GW_ID: self._info.oid,
            CONF_GW_NAME: self._info.name,
            CONF_ENTITIES: entities,
            CONF_SCENES: scenes,
            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
            CONF_LOCAL_SECRET: self._local_secret,
        }

    # ---------------------------------------------------------------- 去重
    def _async_check_duplicate(self, info: GatewayInfo) -> None:
        self._async_abort_entries_match({CONF_GW_ID: info.oid})

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_abort(reason="not_supported")

    # ---------------------------------------------------------------- 选项
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HdlLinkOptionsFlow()


class HdlLinkOptionsFlow(OptionsFlow):
    """已接入后的再配置：改轮询周期。"""

    def __init__(self) -> None:
        self._functions: list[FunctionInfo] = []
        self._scenes: list[dict[str, Any]] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        merged = {**self.config_entry.data, **self.config_entry.options}
        current = merged.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        secret = merged.get(CONF_LOCAL_SECRET, "")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLL_INTERVAL, default=current): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL, step=1, unit_of_measurement="秒",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(CONF_LOCAL_SECRET, default=secret or ""): str,
                }
            ),
            description_placeholders={"host": self.config_entry.data.get(CONF_HOST, "")},
        )
