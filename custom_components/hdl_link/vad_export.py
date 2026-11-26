"""集成内运行时：把勾选的 HA 实体作为一台 LINK 三方设备暴露给网关。

配置来源（选项流里填）：
    vad_enabled / vad_device_name / vad_mac / vad_secret / vad_mac_key / vad_oid / vad_entities

行为：
    入网 → 上报 oid + sid → 收 property/down（调 HA 服务）→ 轮询 HA 状态发 property/up
网关断线或尚未入网时自动退避重试，不拖垮整个集成。
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .const import (
    CONF_VAD_DEVICE_NAME,
    CONF_VAD_ENABLED,
    CONF_VAD_ENTITIES,
    CONF_VAD_MAC,
    CONF_VAD_MAC_KEY,
    CONF_VAD_OID,
    CONF_VAD_SECRET,
    DEFAULT_VAD_OID,
    DOMAIN,
)
from .driver_logic import service_calls
from .vad import VadBridge, VadDevice, VadEntity, ha_state_to_status, make_sid, sign_mac_key

LOGGER = logging.getLogger(__name__)

# HA 域 → HDL spk（拿不到更细信息时的兜底）
DOMAIN_SPK = {
    "light": "light.switch",
    "switch": "other.common",
    "input_boolean": "other.common",
    "fan": "other.common",
    "cover": "curtain.trietex",
    "binary_sensor": "other.common",
}

SENSOR_SPK = {
    "temperature": "sensor.temperature",
    "humidity": "sensor.humidity",
    "illuminance": "sensor.light",
    "carbon_dioxide": "sensor.co2",
    "pm25": "sensor.pm25",
    "tvoc": "sensor.tvoc",
}


def spk_for_entity(hass: HomeAssistant, entity_id: str) -> str:
    """按 HA 实体能力推 HDL spk（只推集成支持的几类）。"""
    state = hass.states.get(entity_id)
    domain = entity_id.split(".")[0]
    if state is not None:
        attrs = state.attributes
        if domain == "light":
            return "light.dimming" if attrs.get("brightness") is not None \
                or "brightness" in (attrs.get("supported_color_modes") or []) else "light.switch"
        if domain == "sensor":
            return SENSOR_SPK.get(str(attrs.get("device_class") or ""), "sensor.temperature")
    return DOMAIN_SPK.get(domain, "")


class VadExporter:
    """生命周期跟随配置条目；一个条目一台虚拟设备。"""

    def __init__(self, hass: HomeAssistant, entry, host: str) -> None:
        self.hass = hass
        self.entry = entry
        self.host = host
        self._task: asyncio.Task | None = None
        self._bridge: VadBridge | None = None
        self._by_sid: dict[str, VadEntity] = {}
        self._unsub: list = []
        self._last_push: dict[str, dict[str, str]] = {}

    # ---------------------------------------------------------------- 组装
    def build_entities(self, oid: str) -> list[VadEntity]:
        entities: list[VadEntity] = []
        for idx, entity_id in enumerate(self._entity_ids(), start=1):
            spk = spk_for_entity(self.hass, entity_id)
            if not spk:
                LOGGER.warning("反向控制：跳过不支持的实体 %s", entity_id)
                continue
            state = self.hass.states.get(entity_id)
            name = str(state.attributes.get("friendly_name") if state else entity_id) or entity_id
            entities.append(VadEntity(
                sid=make_sid(oid, spk, idx),
                name=name,
                spk=spk,
                entity_id=entity_id,
            ))
        self._by_sid = {e.sid: e for e in entities}
        return entities

    def _entity_ids(self) -> list[str]:
        opts = dict(self.entry.options)
        raw = opts.get(CONF_VAD_ENTITIES) or []
        return [str(e) for e in raw if str(e)]

    @staticmethod
    def mac_key(mac: str, secret: str, explicit: str) -> str:
        """平台给的 macKey（或 secret）原样当 auth.mac_key；不再预做 md5。

        vad.py 入网时若被拒（124013）会自己退回 (mac+secret) 两次 md5。
        """
        return (explicit or "").strip() or (secret or "").strip()

    # ---------------------------------------------------------------- 起停
    async def async_start(self) -> None:
        opts = dict(self.entry.options)
        if not opts.get(CONF_VAD_ENABLED):
            return
        mac = str(opts.get(CONF_VAD_MAC) or "").strip().upper()
        secret = str(opts.get(CONF_VAD_SECRET) or "").strip()
        key = self.mac_key(mac, secret, str(opts.get(CONF_VAD_MAC_KEY) or ""))
        if not mac or not key:
            LOGGER.error("反向控制已启用但缺少 mac / macKey / secret，跳过")
            return
        oid = str(opts.get(CONF_VAD_OID) or DEFAULT_VAD_OID).strip().upper()
        entities = self.build_entities(oid)
        if not entities:
            LOGGER.error("反向控制已启用但没有可暴露的实体")
            return

        self._bridge = VadBridge(
            self.host,
            VadDevice(
                oid=oid,
                name=str(opts.get(CONF_VAD_DEVICE_NAME) or "HA Bridge"),
                model="HA-BRIDGE.430",
                mac=mac,
            ),
            entities,
            mac_key=key,
            mac_secret=secret,
            local_secret=None,
            on_down=self._async_on_down,
            state_provider=lambda: {sid: dict(e.status) for sid, e in self._by_sid.items()},
        )
        self._task = self.hass.async_create_background_task(
            self._async_run(), f"{DOMAIN}-vad"
        )
        LOGGER.info("反向控制已启动：设备 %s，%d 个功能点", oid, len(entities))

    async def async_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._bridge is not None:
            await self._bridge.async_close()
            self._bridge = None

    # ---------------------------------------------------------------- 循环
    async def _async_run(self) -> None:
        assert self._bridge is not None
        bridge = self._bridge
        while True:
            try:
                if not bridge.gateway_id:
                    await bridge.async_onboard(timeout=8)
                await bridge.async_connect()
                await bridge.async_upload()
                await self._push_once()
                break
            except Exception as err:  # noqa: BLE001 - 入网/上报失败都退避重试
                LOGGER.warning("反向控制接入失败（60 秒后重试）: %s", err)
                await bridge.async_close()
                await asyncio.sleep(60)

        # 状态变化即推（poll 5s 兜底），并定期心跳
        while True:
            await asyncio.sleep(5)
            try:
                await self._push_once()
                await bridge.async_heartbeat()
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("反向控制上行失败（重新接入）: %s", err)
                await bridge.async_close()
                bridge.gateway_id = ""      # 触发重新入网
                await asyncio.sleep(30)

    async def _push_once(self) -> None:
        assert self._bridge is not None
        changed: dict[str, dict[str, str]] = {}
        for sid, ent in self._by_sid.items():
            state = self.hass.states.get(ent.entity_id)
            mapped = ha_state_to_status(ent.entity_id, ent.spk, state.as_dict() if state else None)
            ent.status.update(mapped)
            if mapped and mapped != self._last_push.get(sid):
                changed[sid] = mapped
        if changed:
            await self._bridge.async_push(changed)
            self._last_push.update(changed)

    # ---------------------------------------------------------------- 下行
    async def _async_on_down(self, sid: str, status: list[dict[str, str]]) -> None:
        ent = self._by_sid.get(sid)
        if ent is None:
            LOGGER.warning("反向控制：未知 sid %s", sid)
            return
        for call in service_calls({"entity_id": ent.entity_id, "spk": ent.spk}, status):
            domain, service = call["service"].split(".", 1)
            await self.hass.services.async_call(
                domain, service, call["data"], blocking=False
            )
        LOGGER.info("Studio 控制 %s → %s", ent.name, status)
