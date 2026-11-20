"""面板用的 REST 接口：扫描 / 写入 / 已同步映射。"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .driver_bridge import (
    async_device_list,
    async_drive_down,
    async_edit_function,
    async_push_driver_config,
)
from .provision import async_mapping, async_provision
from .scan import async_scan

LOGGER = logging.getLogger(__name__)


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> Any:
    """取一个 HDL LINK 协调器（默认第一个网关）。"""
    data = hass.data.get(DOMAIN) or {}
    if entry_id and entry_id in data:
        return data[entry_id]
    if not data:
        return None
    return next(iter(data.values()))


class HdlScanView(HomeAssistantView):
    """GET /api/hdl_link/scan —— 扫描 HA 设备与功能。"""

    url = "/api/hdl_link/scan"
    name = "api:hdl_link:scan"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if _coordinator(hass) is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        try:
            result = await async_scan(hass)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("扫描失败")
            detail = f"{type(err).__name__}: {err}"
            return self.json({"error": detail}, status_code=500)
        return self.json(result)


class HdlProvisionView(HomeAssistantView):
    """POST /api/hdl_link/provision —— 把勾选结果写入网关。"""

    url = "/api/hdl_link/provision"
    name = "api:hdl_link:provision"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("请求体必须是 JSON", status_code=400)

        entry_id = body.get("entry_id")
        coordinator = _coordinator(hass, entry_id)
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)

        devices = body.get("devices") or []
        if not devices:
            return self.json_message("没有勾选任何设备", status_code=400)

        # 默认就是真写：只有显式 dry_run=true 才只生成计划
        dry_run = bool(body.get("dry_run", False))
        if not dry_run and not body.get("confirm"):
            return self.json_message(
                "写入网关需要二次确认：请带上 confirm=true（面板会弹窗确认后自动带上）",
                status_code=400,
            )
        try:
            result = await async_provision(hass, coordinator, {"devices": devices}, dry_run=dry_run)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("写入网关失败")
            return self.json({"error": f"{type(err).__name__}: {err}"}, status_code=500)
        return self.json(result)


class HdlMappingView(HomeAssistantView):
    """GET /api/hdl_link/mapping —— 已同步的 entity→oid/sid 映射。"""

    url = "/api/hdl_link/mapping"
    name = "api:hdl_link:mapping"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        mapping = await async_mapping(hass)
        return self.json({"mapping": mapping, "count": len(mapping)})


class HdlDriverDevicesView(HomeAssistantView):
    """GET /api/hdl_link/driver/devices —— 供 HA 驱动取全量设备清单（含编辑态）。"""

    url = "/api/hdl_link/driver/devices"
    name = "api:hdl_link:driver_devices"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, request.query.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        try:
            result = await async_device_list(hass, coordinator)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("取设备清单失败")
            return self.json({"error": f"{type(err).__name__}: {err}"}, status_code=500)
        return self.json(result)


class HdlDriverDownView(HomeAssistantView):
    """POST /api/hdl_link/driver/down —— 驱动把网关下行转给 HA 执行。

    请求体：{"objects": [{"sid": "...", "status": [{"key": "on_off", "value": "on"}]}]}
    """

    url = "/api/hdl_link/driver/down"
    name = "api:hdl_link:driver_down"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("请求体必须是 JSON", status_code=400)
        coordinator = _coordinator(hass, body.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        objects = body.get("objects") or []
        if not objects:
            return self.json_message("objects 不能为空", status_code=400)
        try:
            result = await async_drive_down(hass, coordinator, objects)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("执行下行失败")
            return self.json({"error": f"{type(err).__name__}: {err}"}, status_code=500)
        return self.json(result)


class HdlDriverEditView(HomeAssistantView):
    """POST /api/hdl_link/driver/edit —— 编辑已注册功能（改名 / 换物模型 / 删除）。"""

    url = "/api/hdl_link/driver/edit"
    name = "api:hdl_link:driver_edit"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("请求体必须是 JSON", status_code=400)
        coordinator = _coordinator(hass, body.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        entity_id = body.get("entity_id") or ""
        action = body.get("action") or ""
        if not entity_id or not action:
            return self.json_message("需要 entity_id 与 action", status_code=400)
        try:
            result = await async_edit_function(
                hass,
                coordinator,
                entity_id=entity_id,
                action=action,
                name=body.get("name"),
                spk=body.get("spk"),
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("编辑功能失败")
            return self.json({"error": f"{type(err).__name__}: {err}"}, status_code=500)
        status = 200 if result.get("ok") else 400
        return self.json(result, status_code=status)


class HdlDriverRegisterView(HomeAssistantView):
    """POST /api/hdl_link/driver/register —— 把 sid 以 driver_name 上报网关（验证驱动是否就绪）。"""

    url = "/api/hdl_link/driver/register"
    name = "api:hdl_link:driver_register"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            body = {}
        coordinator = _coordinator(hass, body.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        driver_name = body.get("driver_name") or "ha"
        result = await async_push_driver_config(hass, coordinator, driver_name)
        return self.json(result, status_code=200 if result.get("ok") else 400)


class HdlBindingView(HomeAssistantView):
    """GET/POST /api/hdl_link/binding —— HDL 功能点 SID ↔ HA 实体 直接绑定。

    GET  → {"sids": {...}, "seen": [...], "modes", "actions", "triggers"}
    POST → {"sid", "entity_id", "mode":"follow|action", "action", "trigger":"any|on|off", "spk"}
    entity_id 传 null 表示删除该 sid 的绑定。
    """

    url = "/api/hdl_link/binding"
    name = "api:hdl_link:binding"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        from .events import async_bindings

        return self.json(await async_bindings(hass))

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        from .events import async_set_mapping

        try:
            body = await request.json()
        except ValueError:
            return self.json_message("请求体必须是 JSON", status_code=400)
        result = await async_set_mapping(
            hass,
            str(body.get("sid") or ""),
            entity_id=body.get("entity_id"),
            mode=str(body.get("mode") or "follow"),
            action=str(body.get("action") or "toggle"),
            trigger=str(body.get("trigger") or "any"),
            spk=body.get("spk"),
            name=body.get("name"),
        )
        return self.json(result, status_code=200 if result.get("ok") else 400)


class HdlEventMapView(HdlBindingView):
    """旧地址 `/api/hdl_link/event_map`，语义已改为功能点直接绑定。"""

    url = "/api/hdl_link/event_map"
    name = "api:hdl_link:event_map"


class HdlBridgeView(HomeAssistantView):
    """GET/POST /api/hdl_link/bridge —— 「按键桥」面板数据 + 一键绑定。

    GET  → {"rows": [...], "counts": {...}, "unbound": [...], "orphan_bindings": [...]}
            rows 同时包含网关里的**场景**（source=scene）与**真实功能点**（source=function），
            每行带绑定状态与「最近一次事件时间/次数」，用来判断面板按键到底有没有到达 HA。
    POST → {"sid", "entity_id"（null=解绑）, "mode":"follow|action", "action", "trigger"}
    """

    url = "/api/hdl_link/bridge"
    name = "api:hdl_link:bridge"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        from .events import async_bindings, join_bridge_rows

        coordinator = _coordinator(hass, request.query.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        try:
            scenes = await coordinator.gateway.async_scenes()
        except Exception as err:  # noqa: BLE001 - 读场景失败不能连累功能点
            LOGGER.warning("读取 HDL 场景失败: %s", err)
            scenes = []
        try:
            functions = [
                row
                for row in (_function_row(fn) for fn in await coordinator.gateway.async_functions())
                if row["sid"] and not row["omodel"].upper().startswith("HA")
            ]
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("读取 HDL 功能点失败: %s", err)
            functions = []
        data = await async_bindings(hass)
        result = join_bridge_rows(scenes, functions, data.get("sids") or {}, data.get("seen") or [])
        result["gateway"] = {"gw_id": coordinator.gw_id, "host": coordinator.gateway.host}
        return self.json(result)

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        from .events import async_bindings, async_set_mapping

        try:
            body = await request.json()
        except ValueError:
            return self.json_message("请求体必须是 JSON", status_code=400)
        coordinator = _coordinator(hass, body.get("entry_id"))
        if coordinator is None:
            return self.json_message("HDL LINK 尚未配置", status_code=400)
        result = await async_set_mapping(
            hass,
            str(body.get("sid") or ""),
            entity_id=body.get("entity_id"),
            mode=str(body.get("mode") or "action"),
            action=str(body.get("action") or "toggle"),
            trigger=str(body.get("trigger") or "any"),
            spk=body.get("spk"),
            name=body.get("name"),
        )
        if not result.get("ok"):
            return self.json(result, status_code=400)
        data = await async_bindings(hass)
        rules = data.get("sids") or {}
        rule = rules.get(str(body.get("sid") or "")) or {}
        return self.json({"ok": True, "sid": body.get("sid"), "rule": rule})


def _function_row(fn: Any) -> dict[str, Any]:
    """网关功能点 → 桥接表行（只保留真实设备，排除本集成自己写进去的 HA 虚拟点）。"""
    omodel = str(getattr(fn, "omodel", "") or "")
    return {
        "sid": str(getattr(fn, "sid", "") or ""),
        "name": str(getattr(fn, "name", "") or ""),
        "oid": str(getattr(fn, "oid", "") or ""),
        "omodel": omodel,
        "spk": str(getattr(fn, "spk", "") or ""),
        "online": bool(getattr(fn, "online", True)),
    }


VIEWS = (
    HdlScanView(),
    HdlProvisionView(),
    HdlMappingView(),
    HdlBindingView(),
    HdlBridgeView(),
    HdlEventMapView(),
    HdlDriverDevicesView(),
    HdlDriverDownView(),
    HdlDriverEditView(),
    HdlDriverRegisterView(),
)
