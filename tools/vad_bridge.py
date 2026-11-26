#!/usr/bin/env python3
"""不装网关驱动的「Studio 控 HA」桥：把 HA 实体伪装成 LINK 本地三方设备。

链路：

    Studio / App / 面板  →  LINK 网关  →  property/down  →  本脚本  →  HA REST  →  HA 实体
                                              ↑                              │
                                        property/up  ←  状态轮询  ←────────┘

用法：

    python tools/vad_bridge.py vad_config.json
    python tools/vad_bridge.py vad_config.json --auth-only   # 只验入网
    python tools/vad_bridge.py vad_config.json --dry-run     # 不调 HA，只打印控制

配置见同目录 vad_config.example.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "hdl_link"))

from driver_logic import service_calls  # noqa: E402
from vad import VadBridge, VadDevice, VadEntity, make_sid, sign_mac_key  # noqa: E402

LOGGER = logging.getLogger("vad_bridge")


# ------------------------------------------------------------------ HA REST
class HaRest:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict | list | None:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read()
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    async def async_state(self, entity_id: str) -> dict | None:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._call, "GET", f"/api/states/{entity_id}", None)
        except urllib.error.URLError as err:
            LOGGER.debug("读 %s 失败: %s", entity_id, err)
            return None

    async def async_call(self, service: str, data: dict) -> bool:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._call, "POST", f"/api/services/{service}", data)
            return True
        except urllib.error.URLError as err:
            LOGGER.warning("调用 %s 失败: %s", service, err)
            return False


# ------------------------------------------------------------------ 状态映射
def ha_to_status(entity_id: str, spk: str, state: dict | None) -> dict[str, str]:
    """HA 状态 → HDL status（只填该 spk 认识的键）。"""
    if not state:
        return {}
    value = str(state.get("state", ""))
    attrs = state.get("attributes") or {}
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    out: dict[str, str] = {}

    if spk.startswith("curtain") or domain == "cover":
        out["on_off"] = {"open": "on", "closed": "off"}.get(value, value if value in ("on", "off") else "")
        if attrs.get("current_position") is not None:
            out["position"] = str(attrs["current_position"])
        return {k: v for k, v in out.items() if v}

    if domain in ("light", "switch", "fan", "input_boolean", "climate", "lock", "valve"):
        out["on_off"] = "on" if value == "on" else "off"
        if domain == "light" and attrs.get("brightness") is not None:
            out["brightness"] = str(round(int(attrs["brightness"]) / 255 * 100))
        return out

    if domain in ("sensor", "binary_sensor"):
        key = {
            "sensor.temperature": "temperature",
            "sensor.humidity": "humidity",
            "sensor.light": "illuminance",
            "sensor.co2": "co2",
            "sensor.pm25": "pm25",
            "sensor.tvoc": "tvoc",
        }.get(spk, "value")
        out[key] = value
    return out


# ------------------------------------------------------------------ 主流程
class Bridge:
    def __init__(self, cfg: dict, *, dry_run: bool = False) -> None:
        dev = cfg["device"]
        oid = dev["oid"].strip()
        self.device = VadDevice(
            oid=oid,
            name=dev.get("name", "HA Bridge"),
            model=dev.get("model", "HA-BRIDGE.430"),
            mac=dev["mac"],
            spk=dev.get("spk", "other.common"),
            fw_version=dev.get("fw_version", "1.0.0"),
            hw_version=dev.get("hw_version", "1.0.0"),
            image_id=dev.get("image_id", "00000"),
        )
        self.entities: list[VadEntity] = []
        for idx, item in enumerate(cfg["entities"]):
            spk = item.get("spk", "other.common")
            sid = (item.get("sid") or make_sid(oid, spk, idx + 1)).strip()
            self.entities.append(VadEntity(
                sid=sid, name=item.get("name", item["entity_id"]),
                spk=spk, entity_id=item["entity_id"],
                attributes=item.get("attributes") or [],
            ))
        self.by_sid = {e.sid: e for e in self.entities}

        ha = cfg.get("ha") or {}
        token = (ha.get("token") or os.environ.get("HA_TOKEN") or "").strip()
        self.ha = None if dry_run or not token else HaRest(ha["base_url"], token)
        self.dry_run = dry_run
        self.poll = float(cfg.get("poll_interval", 5))
        self.heartbeat = float(cfg.get("heartbeat_interval", 30))
        self.last_push: dict[str, dict[str, str]] = {}

        gw = cfg["gateway"]
        self.bridge = VadBridge(
            gw["host"],
            self.device,
            self.entities,
            mac_secret=dev.get("secret"),
            mac_key=dev.get("mac_key"),
            udp_port=int(gw.get("udp_port", 8585)),
            tcp_port=int(gw.get("tcp_port", 8586)),
            local_secret=gw.get("local_secret"),
            on_down=self.on_down,
            state_provider=self.all_states,
        )

    # -------------------------------------------------------------- 回调
    async def on_down(self, sid: str, status: list[dict[str, str]]) -> None:
        ent = self.by_sid.get(sid)
        if ent is None:
            LOGGER.warning("未知 sid %s，忽略下发 %s", sid, status)
            return
        calls = service_calls({"entity_id": ent.entity_id, "spk": ent.spk}, status)
        for call in calls:
            if self.ha is None:
                LOGGER.info("[dry-run] %s %s", call["service"], call["data"])
                continue
            await self.ha.async_call(call["service"], call["data"])

    async def all_states(self) -> dict[str, dict[str, str]]:
        if self.ha is None:
            return {e.sid: dict(e.status) for e in self.entities}
        states = await asyncio.gather(*(self.ha.async_state(e.entity_id) for e in self.entities))
        out: dict[str, dict[str, str]] = {}
        for ent, st in zip(self.entities, states):
            mapped = ha_to_status(ent.entity_id, ent.spk, st)
            if mapped:
                out[ent.sid] = mapped
        return out

    # -------------------------------------------------------------- 循环
    async def run(self, auth_only: bool = False) -> None:
        await self.bridge.async_onboard()
        if auth_only:
            LOGGER.info("入网通过：gatewayId=%s oid=%s", self.bridge.gateway_id,
                        self.bridge.gateway_oid)
            return
        await self.bridge.async_connect()
        await self.bridge.async_upload()
        await self.push_once()
        tasks = [asyncio.create_task(self.state_loop())]
        if self.heartbeat > 0:
            tasks.append(asyncio.create_task(self.heartbeat_loop()))
        await asyncio.gather(*tasks)

    async def push_once(self) -> None:
        states = await self.all_states()
        changed = {sid: st for sid, st in states.items()
                   if st and st != self.last_push.get(sid)}
        if not changed:
            return
        await self.bridge.async_push(changed)
        self.last_push.update(changed)
        LOGGER.debug("上行 %d 个功能点状态", len(changed))

    async def state_loop(self) -> None:
        while True:
            await asyncio.sleep(max(self.poll, 1))
            try:
                await self.push_once()
            except Exception as err:  # noqa: BLE001 - 网络抖动不该退出
                LOGGER.warning("上行失败: %s", err)

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            await self.bridge.async_heartbeat()


def main() -> int:
    parser = argparse.ArgumentParser(description="LINK 本地三方设备桥（无驱动把 HA 暴露给 Studio）")
    parser.add_argument("config", help="配置文件路径")
    parser.add_argument("--auth-only", action="store_true", help="只做入网认证验证")
    parser.add_argument("--dry-run", action="store_true", help="不调 HA，只打印下行控制")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    try:
        asyncio.run(Bridge(cfg, dry_run=args.dry_run).run(auth_only=args.auth_only))
    except KeyboardInterrupt:
        return 0
    except Exception as err:  # noqa: BLE001 - CLI 顶层兜底
        LOGGER.error("失败: %s", err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
