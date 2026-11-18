"""HDL 网关的 LINK 协议封装：发现、读工程、读写属性、执行场景。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .const import (
    READ_BATCH,
    TOPIC_DEVICE_LIST,
    TOPIC_FUNCTION_ATTR,
    TOPIC_FUNCTION_LIST,
    TOPIC_GATEWAY_GET,
    TOPIC_GATEWAY_SEARCH,
    TOPIC_HEARTBEAT,
    TOPIC_PROPERTY_DOWN,
    TOPIC_PROPERTY_READ,
    TOPIC_ROOM_LIST,
    TOPIC_SCENE_EXECUTE,
    TOPIC_SCENE_LIST,
)
from .protocol import LinkClient, LinkError

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GatewayInfo:
    """网关基本信息（gateway/search 与 gateway/get 的 objects）。"""

    host: str
    oid: str
    name: str
    model: str
    mac: str
    gateway_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FunctionInfo:
    """网关内的一个功能（一个可控/可读的最小单元）。"""

    sid: str
    oid: str
    name: str
    spk: str
    omodel: str = ""
    online: bool = True
    attributes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def attr_keys(self) -> list[str]:
        return [str(a.get("key")) for a in self.attributes]


class HdlGateway:
    """一个网关连接上的 LINK 操作集合（异步、非阻塞）。"""

    def __init__(
        self, host: str, oid: str | None = None, port: int = 8586, local_secret: str | None = None
    ) -> None:
        self.host = host
        self.oid = oid or ""
        self.client = LinkClient(host, port, local_secret=local_secret)
        self.info: GatewayInfo | None = None

    # -------------------------------------------------------------- 发现
    @staticmethod
    async def async_search(host: str, port: int = 8586, timeout: float = 4.0) -> GatewayInfo | None:
        """LINK 广播搜索：对指定主机发 /user/all/custom/gateway/search。"""
        client = LinkClient(host, port, timeout=timeout)
        try:
            resp = await client.async_request(TOPIC_GATEWAY_SEARCH, timeout=timeout)
        except LinkError:
            return None
        finally:
            await client.async_close()
        if resp.get("code") not in ("0", "200", 0, 200):
            return None
        obj = resp.get("objects") or {}
        if isinstance(obj, list):
            obj = obj[0] if obj else {}
        if not obj:
            return None
        return GatewayInfo(
            host=host,
            oid=str(obj.get("oid", "")),
            name=str(obj.get("device_name", obj.get("device_model", host))),
            model=str(obj.get("device_model", "")),
            mac=str(obj.get("device_mac", "")),
            gateway_id=str(obj.get("gatewayId", "")),
            raw=obj,
        )

    # -------------------------------------------------------------- 连接
    async def async_connect(self) -> GatewayInfo:
        await self.client.async_connect()
        if self.info is None:
            if not self.oid:
                searched = await self.async_search(self.host)
                if searched is None:
                    raise LinkError(f"{self.host} 不是可用的 HDL LINK 网关")
                self.info = searched
                self.oid = searched.oid
            else:
                self.info = await self.async_search(self.host)
                if self.info is None:
                    # 搜索失败也允许继续，用配置里的 oid
                    self.info = GatewayInfo(self.host, self.oid, self.oid, "", "")
        if self.info and self.info.oid:
            self.oid = self.info.oid
        return self.info

    async def async_close(self) -> None:
        await self.client.async_close()

    # -------------------------------------------------------------- 读工程
    async def async_gateway_detail(self) -> dict[str, Any]:
        topic = TOPIC_GATEWAY_GET.format(gw=self.oid)
        resp = await self.client.async_request(topic, {"objects": {}})
        return resp.get("objects") or {}

    async def async_devices(self) -> list[dict[str, Any]]:
        """物理模块列表（device/list/get）。"""
        resp = await self.client.async_request(TOPIC_DEVICE_LIST.format(gw=self.oid))
        objs = resp.get("objects") or []
        return objs if isinstance(objs, list) else []

    async def async_functions(self) -> list[FunctionInfo]:
        """功能列表（function/list/get）：一次可能返回不全，重复取直到不再新增。"""
        collected: dict[str, FunctionInfo] = {}
        for _ in range(10):
            resp = await self.client.async_request(
                TOPIC_FUNCTION_LIST.format(gw=self.oid), timeout=15
            )
            objs = resp.get("objects") or []
            if isinstance(objs, dict):
                objs = objs.get("objects") or []
            new = 0
            for obj in objs if isinstance(objs, list) else []:
                sid = str(obj.get("sid", ""))
                if not sid or sid in collected:
                    continue
                collected[sid] = FunctionInfo(
                    sid=sid,
                    oid=str(obj.get("oid", "")),
                    name=str(obj.get("name", sid)),
                    spk=str(obj.get("spk", "")),
                    omodel=str(obj.get("omodel", "")),
                    online=str(obj.get("online", "true")).lower() != "false",
                )
                new += 1
            if new == 0:
                break
        return list(collected.values())

    async def async_function_attributes(self, sids: list[str]) -> list[dict[str, Any]]:
        """读取功能属性定义（function/attribute/get）。"""
        resp = await self.client.async_request(
            TOPIC_FUNCTION_ATTR.format(gw=self.oid),
            {"objects": [{"sid": sid} for sid in sids]},
            timeout=15,
        )
        objs = resp.get("objects") or []
        return objs if isinstance(objs, list) else []

    async def async_rooms(self) -> list[dict[str, Any]]:
        resp = await self.client.async_request(TOPIC_ROOM_LIST.format(gw=self.oid))
        objs = resp.get("objects") or []
        return objs if isinstance(objs, list) else []

    async def async_scenes(self) -> list[dict[str, Any]]:
        resp = await self.client.async_request(TOPIC_SCENE_LIST.format(gw=self.oid))
        objs = resp.get("objects") or []
        return [o for o in objs if isinstance(o, dict)] if isinstance(objs, list) else []

    # -------------------------------------------------------------- 读写属性
    async def async_read_properties(self, sids: list[str]) -> dict[str, dict[str, str]]:
        """批量读属性：返回 {sid: {key: value}}。"""
        result: dict[str, dict[str, str]] = {}
        for start in range(0, len(sids), READ_BATCH):
            batch = sids[start:start + READ_BATCH]
            try:
                resp = await self.client.async_request(
                    TOPIC_PROPERTY_READ.format(gw=self.oid),
                    {"objects": [{"sid": sid} for sid in batch]},
                    timeout=12,
                )
            except LinkError as err:
                LOGGER.warning("读取属性失败（%d 个 sid）: %s", len(batch), err)
                raise
            for obj in resp.get("objects") or []:
                sid = str(obj.get("sid", ""))
                if not sid:
                    continue
                values: dict[str, str] = {}
                for item in obj.get("status") or []:
                    if isinstance(item, dict) and item.get("key") is not None:
                        values[str(item["key"])] = str(item.get("value"))
                result[sid] = values
        return result

    async def async_set_property(self, sid: str, status: list[dict[str, str]]) -> bool:
        """下发属性（控制报文），返回是否被网关确认。"""
        resp = await self.client.async_request(
            TOPIC_PROPERTY_DOWN.format(gw=self.oid),
            {"objects": [{"sid": sid, "status": status}]},
            timeout=8,
        )
        ok = resp.get("code") in ("0", "200", 0, 200)
        if not ok:
            LOGGER.warning("控制 %s 失败: %s", sid, resp)
        return ok

    async def async_execute_scene(self, scene_sid: str) -> bool:
        resp = await self.client.async_request(
            TOPIC_SCENE_EXECUTE.format(gw=self.oid),
            {"objects": [{"sid": scene_sid}]},
            timeout=8,
        )
        return resp.get("code") in ("0", "200", 0, 200)

    async def async_heartbeat(self) -> None:
        try:
            await self.client.async_request(TOPIC_HEARTBEAT.format(gw=self.oid), timeout=5)
        except LinkError as err:
            LOGGER.debug("心跳失败: %s", err)
