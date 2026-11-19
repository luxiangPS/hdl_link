"""HDL LINK 集成入口：一个配置条目 = 一个 HDL 主网关。

除了实体平台，还注册：
- REST 接口 /api/hdl_link/scan | provision | mapping
- 侧边栏面板「HDL 设备同步」（扫描 HA 设备 → 勾选 → 写入 HDL 网关）
- 服务 hdl_link.scan / hdl_link.provision（供自动化与脚本调用）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_GW_ID, DOMAIN, MANUFACTURER
from .coordinator import HdlCoordinator
from .http_api import VIEWS
from .protocol import LinkError
from .provision import async_provision
from .scan import async_scan

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

PANEL_URL_PATH = "hdl-device-sync"
PANEL_COMPONENT = "hdl-link-provision"
STATIC_URL = "/hdl_link_static"
PANEL_JS = Path(__file__).parent / "frontend"
PANEL_VERSION = "2.3.2"

SERVICE_SCAN = "scan"
SERVICE_PROVISION = "provision"

SCAN_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})
PROVISION_SCHEMA = vol.Schema(
    {
        vol.Required("devices"): vol.All(cv.ensure_list, [dict]),
        vol.Optional("dry_run", default=True): cv.boolean,
        vol.Optional("entry_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigEntry) -> bool:
    """集成装载时注册静态资源/面板/接口/服务（必须在 HTTP 服务启动前完成）。"""
    await _async_setup_provision(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立 LINK 连接并加载平台。"""
    coordinator = HdlCoordinator(hass, entry)

    try:
        await coordinator.async_connect()
    except (LinkError, OSError) as err:
        LOGGER.error("连接 HDL 网关 %s 失败: %s", entry.data.get("host"), err)
        raise ConfigEntryNotReady(f"无法连接 HDL 网关 {entry.data.get('host')}: {err}") from err

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

    await _async_setup_provision(hass)

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


# ------------------------------------------------------------------ 同步能力（只注册一次）
async def _async_setup_provision(hass: HomeAssistant) -> None:
    """注册静态资源、REST 接口、侧边栏面板与两个服务。"""
    flag_key = f"{DOMAIN}_provision_ready"
    static_key = f"{DOMAIN}_static_ready"
    if hass.data.get(flag_key):
        return
    hass.data[flag_key] = True

    # 1) 面板前端静态文件（必须在 HTTP 服务启动阶段注册，晚于启动会被拒绝）
    if not hass.data.get(static_key):
        hass.data[static_key] = True
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(STATIC_URL, str(PANEL_JS), cache_headers=False)]
            )
        except RuntimeError as err:  # 已注册或 HTTP 已启动
            LOGGER.warning("静态路径注册失败（面板 JS 可能 404）: %s", err)

    # 2) REST 接口
    for view in VIEWS:
        try:
            hass.http.register_view(view)
        except Exception as err:  # noqa: BLE001 - 已注册则跳过
            LOGGER.debug("REST 视图已存在: %s", err)

    # 3) 侧边栏面板
    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title="HDL 设备同步",
            sidebar_icon="mdi:swap-horizontal",
            module_url=f"{STATIC_URL}/provision.js?v={PANEL_VERSION}",
            require_admin=False,
            embed_iframe=False,
        )
        LOGGER.info("已注册侧边栏面板 %s -> %s", PANEL_URL_PATH, f"{STATIC_URL}/provision.js")
    except Exception as err:  # noqa: BLE001 - 面板注册失败不影响服务
        LOGGER.warning("注册 HDL 设备同步面板失败: %s", err)

    # 4) 服务
    def _coordinator(entry_id: str | None = None) -> HdlCoordinator | None:
        data = hass.data.get(DOMAIN) or {}
        if entry_id and entry_id in data:
            return data[entry_id]
        return next(iter(data.values())) if data else None

    async def _handle_scan(call: ServiceCall) -> dict:
        return await async_scan(hass)

    async def _handle_provision(call: ServiceCall) -> dict:
        coordinator = _coordinator(call.data.get("entry_id"))
        if coordinator is None:
            raise ValueError("HDL LINK 尚未配置")
        return await async_provision(
            hass,
            coordinator,
            {"devices": call.data["devices"]},
            dry_run=call.data.get("dry_run", True),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SCAN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCAN,
            _handle_scan,
            schema=SCAN_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_PROVISION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_PROVISION,
            _handle_provision,
            schema=PROVISION_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


__all__ = ["async_setup_entry", "async_unload_entry", "CONF_GW_ID"]





