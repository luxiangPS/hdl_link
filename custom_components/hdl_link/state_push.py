"""HA 实体状态变更 → 立刻用 `property/up` 同步到 HDL 网关。

规则（用户要求的口径）：
- 只同步**确实还存在于网关里**的 sid（按 SID 判断，网关里没有就跳过）；
- 状态一变就推，不等 30 秒心跳（心跳那套仍然保留，作为兜底/保活）；
- 同一实体在 DEBOUNCE 秒内多次变化只推最后一次，避免调光拖动刷爆网关。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from . import bridge
from .const import TOPIC_PROPERTY_UP
from .provision import async_mapping

LOGGER = logging.getLogger(__name__)

DEBOUNCE = 0.4        # 秒，合并连续变化（调光拖动）
SID_CHECK_INTERVAL = 300   # 秒，定期重读网关功能点列表（判断 sid 是否还在）


class _StatePusher:
    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.mapping: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self._sids_dirty = True

    # -------------------------------------------------------------- 映射
    async def async_load_mapping(self) -> dict[str, dict[str, Any]]:
        """只保留网关里真实存在的 sid。"""
        raw = await async_mapping(self.hass)
        sids = self.coordinator.function_sids
        if not sids:
            sids = await self.coordinator.async_refresh_function_sids()
        self.mapping = {
            entity_id: info
            for entity_id, info in raw.items()
            if str(info.get("sid") or "") in sids
        }
        missing = len(raw) - len(self.mapping)
        if missing:
            LOGGER.info("跳过 %d 条映射：对应 sid 已不在网关里", missing)
        return self.mapping

    # -------------------------------------------------------------- 推送
    async def async_push_entity(self, entity_id: str) -> bool:
        info = self.mapping.get(entity_id)
        if not info:
            return False
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        sid = str(info.get("sid") or "")
        spk = str(info.get("spk") or "")
        if not sid or not spk:
            return False
        status = bridge.status_from_state(spk, state.state, dict(state.attributes))
        if not status:
            return False
        gateway = self.coordinator.gateway
        try:
            resp = await gateway.client.async_request(
                TOPIC_PROPERTY_UP.format(gw=gateway.oid),
                bridge.up_body(sid, status),
                timeout=8,
            )
            ok = str(resp.get("code") or "0") in ("0", "200")
            if not ok:
                LOGGER.debug("状态同步 %s 返回 %s", entity_id, resp)
            return ok
        except Exception as err:  # noqa: BLE001 - 上报失败不影响实体
            LOGGER.debug("状态同步 %s 失败: %s", entity_id, err)
            return False

    @callback
    def _on_state_changed(self, event: Any) -> None:
        entity_id = event.data.get("entity_id")
        if not entity_id or entity_id not in self.mapping:
            return
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if new is None or (old is not None and not _meaningful_change(old, new)):
            return
        task = self._pending.get(entity_id)
        if task is not None and not task.done():
            task.cancel()
        self._pending[entity_id] = self.hass.async_create_task(
            self._async_debounced(entity_id)
        )

    async def _async_debounced(self, entity_id: str) -> None:
        try:
            await asyncio.sleep(DEBOUNCE)
        except asyncio.CancelledError:
            return
        try:
            await self.async_push_entity(entity_id)
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("状态同步异常 %s: %s", entity_id, err)
        finally:
            self._pending.pop(entity_id, None)

    async def async_push_all(self) -> int:
        pushed = 0
        for entity_id in list(self.mapping):
            if await self.async_push_entity(entity_id):
                pushed += 1
        return pushed

    # -------------------------------------------------------------- 循环
    async def _async_sid_watch(self) -> None:
        while True:
            await asyncio.sleep(SID_CHECK_INTERVAL)
            try:
                await self.coordinator.async_refresh_function_sids()
                await self.async_load_mapping()
            except Exception as err:  # noqa: BLE001
                LOGGER.debug("重读网关功能点失败: %s", err)

    async def async_start(self) -> None:
        await self.async_load_mapping()
        await self.async_push_all()          # 启动先全量上报一次（上线 + 状态对齐）
        self._task = self.hass.async_create_task(self._async_sid_watch())
        LOGGER.info("状态同步已启动：%d 个实体（状态变更即上报）", len(self.mapping))


def _meaningful_change(old: Any, new: Any) -> bool:
    """状态或关键属性变了才算一次变化（避免 last_updated 之类的噪声）。"""
    if old.state != new.state:
        return True
    for key in ("brightness", "color_temp_kelvin", "current_position", "rgb_color", "fan_mode"):
        if old.attributes.get(key) != new.attributes.get(key):
            return True
    return False


async def async_setup_state_push(hass: HomeAssistant, coordinator: Any) -> _StatePusher:
    """注册监听并启动。返回 pusher，供卸载时反注册。"""
    pusher = _StatePusher(hass, coordinator)
    await pusher.async_start()
    entity_ids = list(pusher.mapping)
    if entity_ids:
        pusher._unsub = async_track_state_change_event(
            hass, entity_ids, pusher._on_state_changed
        )
    return pusher


def async_unload_state_push(pusher: _StatePusher | None) -> None:
    if pusher is None:
        return
    unsub = getattr(pusher, "_unsub", None)
    if unsub is not None:
        unsub()
    if pusher._task is not None:
        pusher._task.cancel()
