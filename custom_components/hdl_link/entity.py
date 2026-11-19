"""实体基类：按网关节点的物理模块分组，状态取自协调器缓存。"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, ON_VALUES
from .coordinator import HdlCoordinator


class HdlEntity(CoordinatorEntity[HdlCoordinator]):
    """所有 HDL 实体的公共部分。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HdlCoordinator, config: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.sid: str = str(config["sid"])
        self.spk: str = str(config.get("spk") or "")
        self.oid: str = str(config.get("oid") or "")
        raw_name = str(config.get("name") or "").strip()
        # 优先用协调器算好的唯一展示名（同一设备内重名会自动补通道号）
        self.func_name = coordinator.display_names.get(self.sid) or ""
        if not self.func_name:
            if raw_name:
                self.func_name = raw_name
            else:
                # 工程里没给功能起名时，用通道号生成可读名称，避免出现 48 位 sid
                channel = channel_from_sid(self.sid)
                self.func_name = f"通道 {channel:02d}" if channel is not None else self.sid
        self._attr_unique_id = f"{coordinator.gw_id}-{self.sid}-{key}"
        self._attr_name = None  # 用设备名 + 实体名组合

    # ------------------------------------------------------------------ 设备
    @property
    def device_info(self) -> DeviceInfo:
        dev = self.coordinator.devices.get(self.oid, {})
        model = dev.get("device_model") or self.coordinator.entities.get(self.sid, {}).get("omodel") or "HDL 功能模块"
        name = dev.get("device_name") or model
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.gw_id}-{self.oid or self.sid}")},
            manufacturer=MANUFACTURER,
            model=str(model),
            name=str(name),
            sw_version=str(dev.get("fw_version") or "") or None,
            via_device=(DOMAIN, self.coordinator.gw_id),
        )

    @property
    def gateway_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.gw_id)},
            manufacturer=MANUFACTURER,
            model="HDL 主网关（LINK）",
            name=self.coordinator.gw_name,
            configuration_url=f"http://{self.coordinator.gateway.host}",
        )

    # ------------------------------------------------------------------ 状态
    @property
    def values(self) -> dict[str, str]:
        return (self.coordinator.data or {}).get(self.sid) or {}

    @property
    def online(self) -> bool:
        return True

    def value(self, *keys: str) -> str | None:
        for key in keys:
            if key in self.values:
                return self.values[key]
        return None

    def on_state(self, *keys: str) -> bool | None:
        """按 on_off 类键判断开/关（不与实体的 is_on 属性冲突）。"""
        raw = self.value(*(keys or ("on_off",)))
        if raw is None:
            return None
        return str(raw).strip().lower() in ON_VALUES

    # ------------------------------------------------------------------ 写属性
    async def async_write(self, **attrs: str) -> None:
        status = [{"key": k, "value": str(v)} for k, v in attrs.items() if v is not None]
        if not status:
            return
        await self.coordinator.async_write(self.sid, status)


def channel_from_sid(sid: str) -> int | None:
    """从功能 sid 里取出通道号：<oid:16><模块:4><通道:4><后缀:4>。"""
    if len(sid) < 24:
        return None
    try:
        return int(sid[20:24], 16)
    except ValueError:
        return None


def numeric(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
