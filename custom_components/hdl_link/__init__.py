"""HDL LINK 集成入口：一个配置条目 = 一个 HDL 主网关。

纯本地对接模式（对齐 HDL 开放平台的「本地三方应用」方式）：
- 直接与网关建立本地连接，**不在网关侧安装任何驱动**
- 读功能点 / 属性定义 / 场景 / 房间，并下发控制
- 状态靠周期读 + 网关可选的主动上报（配了本地密钥才解得上报）

保留的服务：
- hdl_link.bind        面板按键 / 场景 sid 绑定到 HA 实体或动作
- hdl_link.scene_list  列出网关场景 + 当前绑定情况（排查用）
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_GW_ID, DOMAIN, MANUFACTURER
from .coordinator import HdlCoordinator
from .protocol import LinkError
from .vad_export import VadExporter

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

SERVICE_BIND = "bind"
SERVICE_SCENE_LIST = "scene_list"
SERVICE_SCENE_BRIDGE = "scene_bridge"
SERVICE_SCENE_BRIDGE_REMOVE = "scene_bridge_remove"

SCAN_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})
SCENE_BRIDGE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("name"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)
BIND_SCHEMA = vol.Schema(
    {
        vol.Required("sid"): cv.string,
        vol.Optional("entity_id"): vol.Any(cv.string, None),
        vol.Optional("mode", default="action"): vol.In(["follow", "action"]),
        vol.Optional("action", default="toggle"): cv.string,
        vol.Optional("trigger", default="any"): vol.In(["any", "on", "off"]),
        vol.Optional("spk"): cv.string,
        vol.Optional("name"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigEntry) -> bool:
    """注册服务（不依赖 HTTP / 面板，启动顺序无关）。"""
    _async_register_services(hass)
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

    async def _heartbeat_loop() -> None:
        """心跳保活 + 兜底刷新（网关不主动上报时靠这里把状态拉回来）。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await coordinator.gateway.async_heartbeat()
            try:
                await coordinator.async_poll_now()
            except Exception as err:  # noqa: BLE001 - 保活失败不能中断循环
                LOGGER.warning("周期刷新失败: %s", err)

    entry.async_create_background_task(hass, _heartbeat_loop(), f"{DOMAIN}-heartbeat")

    # 无驱动反向控制（可选）：把勾选的 HA 实体作为一台 LINK 三方设备暴露给网关
    exporter = VadExporter(hass, entry, coordinator.gateway.host)
    await exporter.async_start()
    hass.data.setdefault(DOMAIN, {})[f"{entry.entry_id}_vad"] = exporter
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载：停平台、停反向控制、关连接。"""
    exporter = hass.data.get(DOMAIN, {}).pop(f"{entry.entry_id}_vad", None)
    if exporter is not None:
        await exporter.async_stop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: HdlCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_close()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """允许在设备页直接删掉网关里已不存在的残留设备（历史会话/换网关留下的影子设备）。"""
    LOGGER.info("移除 HDL LINK 残留设备: %s", device_entry.name)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """注册 bind / scene_list 两个服务（只注册一次）。"""
    flag_key = f"{DOMAIN}_services_ready"
    if hass.data.get(flag_key):
        return
    hass.data[flag_key] = True

    def _coordinator(entry_id: str | None = None) -> HdlCoordinator | None:
        data = hass.data.get(DOMAIN) or {}
        if entry_id and entry_id in data:
            return data[entry_id]
        return next(iter(data.values())) if data else None

    async def _handle_bind(call: ServiceCall) -> dict:
        from .events import async_set_mapping

        return await async_set_mapping(
            hass,
            str(call.data["sid"]),
            entity_id=call.data.get("entity_id"),
            mode=call.data.get("mode", "action"),
            action=call.data.get("action", "toggle"),
            trigger=call.data.get("trigger", "any"),
            spk=call.data.get("spk"),
            name=call.data.get("name"),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_BIND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_BIND,
            _handle_bind,
            schema=BIND_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _handle_scene_list(call: ServiceCall) -> dict:
        """列出网关里的场景 + 每个场景的 HA 绑定情况（排查「面板按了没反应」用）。"""
        from .events import async_bindings, join_scene_bindings

        coordinator = _coordinator(call.data.get("entry_id"))
        if coordinator is None:
            raise ValueError("HDL LINK 尚未配置")
        scenes = await coordinator.gateway.async_scenes()
        data = await async_bindings(hass)
        return join_scene_bindings(scenes, data.get("sids") or {}, data.get("seen") or [])

    if not hass.services.has_service(DOMAIN, SERVICE_SCENE_LIST):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCENE_LIST,
            _handle_scene_list,
            schema=SCAN_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _handle_scene_bridge(call: ServiceCall) -> dict:
        """给一个 HA 实体在网关里建一对「开/关」场景并绑定。

        网关不会把虚拟设备的控制转发给三方设备（实测只回执、不投递），
        但「场景执行」一定会广播 scene/status/up，所以 Studio / App / 面板里点这对
        场景就等于控制 HA 实体，不需要在网关装任何驱动。
        """
        from .events import async_set_mapping
        from .scene_bridge import scene_sids

        coordinator = _coordinator(call.data.get("entry_id"))
        if coordinator is None:
            raise ValueError("HDL LINK 尚未配置")
        entity_id = str(call.data["entity_id"])
        state = hass.states.get(entity_id)
        label = str(call.data.get("name") or (state.attributes.get("friendly_name") if state else "") or entity_id)
        sid_on, sid_off = scene_sids(entity_id)
        results = []
        for sid, scene_name, action in (
            (sid_on, f"{label} 开", "turn_on"),
            (sid_off, f"{label} 关", "turn_off"),
        ):
            resp = await coordinator.gateway.async_scene_add(sid, scene_name)
            code = str(resp.get("code") or "")
            results.append({"sid": sid, "name": scene_name, "add_code": code,
                            "add_msg": resp.get("msg")})
            if code not in ("0", "", "500"):
                continue
            await async_set_mapping(
                hass, sid, entity_id=entity_id, mode="action", action=action, name=scene_name
            )
            await asyncio.sleep(1)
        # 建完回读一遍，确认两条场景都真的落到网关里（网关会静默丢弃不完整的 add）
        try:
            scenes = await coordinator.gateway.async_scenes()
        except Exception:  # noqa: BLE001
            scenes = []
        existing = {str(s.get("sid") or "") for s in scenes}
        for item in results:
            item["present"] = item["sid"] in existing
        return {"entity_id": entity_id, "label": label, "scenes": results}

    async def _handle_scene_bridge_remove(call: ServiceCall) -> dict:
        """拆掉某个 HA 实体的场景桥：删网关里这对场景，并清掉绑定。"""
        from .events import async_set_mapping
        from .scene_bridge import scene_sids

        coordinator = _coordinator(call.data.get("entry_id"))
        if coordinator is None:
            raise ValueError("HDL LINK 尚未配置")
        entity_id = str(call.data["entity_id"])
        removed = []
        for sid in scene_sids(entity_id):
            resp = await coordinator.gateway.async_scene_delete(sid)
            removed.append({"sid": sid, "code": str(resp.get("code") or "")})
            await async_set_mapping(hass, sid, entity_id=None)
            await asyncio.sleep(1)
        return {"entity_id": entity_id, "removed": removed}

    if not hass.services.has_service(DOMAIN, SERVICE_SCENE_BRIDGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCENE_BRIDGE,
            _handle_scene_bridge,
            schema=SCENE_BRIDGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SCENE_BRIDGE_REMOVE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCENE_BRIDGE_REMOVE,
            _handle_scene_bridge_remove,
            schema=SCENE_BRIDGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


__all__ = ["async_setup_entry", "async_unload_entry", "CONF_GW_ID"]
