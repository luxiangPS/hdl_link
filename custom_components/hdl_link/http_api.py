"""面板用的 REST 接口：扫描 / 写入 / 已同步映射。"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
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
            return self.json_message(f"扫描失败: {err}", status_code=500)
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

        dry_run = bool(body.get("dry_run", True))
        try:
            result = await async_provision(hass, coordinator, {"devices": devices}, dry_run=dry_run)
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("写入网关失败")
            return self.json_message(f"写入网关失败: {err}", status_code=500)
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


VIEWS = (HdlScanView(), HdlProvisionView(), HdlMappingView())
