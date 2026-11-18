"""数据协调器：一个配置条目一个网关连接；状态由网关主动上报驱动。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ENTITIES,
    CONF_GW_ID,
    CONF_GW_NAME,
    CONF_HOST,
    CONF_LOCAL_SECRET,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)
from .gateway import HdlGateway
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)


class HdlCoordinator(DataUpdateCoordinator[dict[str, dict[str, str]]]):
    """持有网关连接与全部实体的实时属性。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        raw_interval = data.get(CONF_POLL_INTERVAL)
        try:
            interval = int(raw_interval) if raw_interval not in (None, "") else DEFAULT_POLL_INTERVAL
        except (TypeError, ValueError):
            interval = DEFAULT_POLL_INTERVAL
        # 0 表示不做周期轮询：网关会主动上报状态变化，实体只在收到上报或手动刷新时更新
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}-{data.get(CONF_HOST)}",
            update_interval=timedelta(seconds=interval) if interval > 0 else None,
        )
        self.entry = entry
        self.gw_id: str = str(data.get(CONF_GW_ID) or "")
        self.gw_name: str = str(data.get(CONF_GW_NAME) or data.get(CONF_HOST))
        self.gateway = HdlGateway(
            str(data[CONF_HOST]),
            self.gw_id,
            int(data.get(CONF_PORT, DEFAULT_PORT)),
            local_secret=str(data.get(CONF_LOCAL_SECRET) or "") or None,
        )
        # sid -> 实体配置
        self.entities: dict[str, dict[str, Any]] = {
            str(item["sid"]): item for item in data.get(CONF_ENTITIES, []) if item.get("sid")
        }
        # 场景列表
        self.scenes: list[dict[str, Any]] = list(data.get("scenes") or [])
        # oid -> 物理模块信息
        self.devices: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 连接
    async def async_connect(self) -> None:
        # 网关主动上报（已解密）直接并入实体状态；默认不轮询，只在启动与手动刷新时读取全量
        self.gateway.client.push_handler = self._on_push
        info = await self.gateway.async_connect()
        if info and info.oid:
            self.gw_id = info.oid
        try:
            for item in await self.gateway.async_devices():
                oid = str(item.get("oid") or "")
                if oid:
                    self.devices[oid] = item
        except LinkError as err:
            LOGGER.debug("读取物理模块列表失败（不影响实体）: %s", err)

    async def async_close(self) -> None:
        await self.gateway.async_close()

    def _on_push(self, objects: list[dict[str, Any]]) -> None:
        """处理网关上报：把 status 合并进实体状态并推给实体。"""
        data = dict(self.data or {})
        changed = False
        for obj in objects:
            sid = str(obj.get("sid") or "")
            if not sid or sid not in self.entities:
                continue
            status = obj.get("status") or []
            values = {str(x.get("key")): str(x.get("value")) for x in status if x.get("key")}
            if not values:
                continue
            current = dict(data.get(sid) or {})
            if current == {**current, **values}:
                continue
            current.update(values)
            data[sid] = current
            changed = True
        if changed:
            self.async_set_updated_data(data)

    # ------------------------------------------------------------------ 轮询
    async def _async_update_data(self) -> dict[str, dict[str, str]]:
        sids = list(self.entities.keys())
        if not sids:
            return {}
        try:
            values = await self.gateway.async_read_properties(sids)
        except LinkError as err:
            raise UpdateFailed(str(err)) from err
        # 合并最近一次已知值，避免网关偶发漏报导致实体变空
        merged = dict(self.data or {})
        merged.update(values)
        return {sid: merged.get(sid, {}) for sid in sids}

    async def async_poll_now(self) -> None:
        await self.async_refresh()

    # ------------------------------------------------------------------ 控制
    async def async_write(self, sid: str, status: list[dict[str, str]]) -> bool:
        """下发属性并立即回读该功能，保证 UI 状态跟手。"""
        ok = await self.gateway.async_set_property(sid, status)
        try:
            fresh = await self.gateway.async_read_properties([sid])
            if fresh.get(sid):
                data = dict(self.data or {})
                data[sid] = fresh[sid]
                self.async_set_updated_data(data)
        except LinkError:
            pass
        return ok
