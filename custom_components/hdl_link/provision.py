"""把 HA 侧勾选的设备与功能写入 HDL 网关（物理模型 oid + 功能点 sid）。

实测结论（2026-11-19，MGWIP.430 / 010105003DF6F100）：
- 读：device/list/get、function/list/get、function/attribute/get 走 TCP 8586 稳定可用；
- 写：device/add 在本地 TCP 上返回 code=0，但设备列表不变化（网关不落库），
      且 remoteconfig/edit（文档标注"远程时 MQTT 通讯时使用"）在本地 TCP 无响应。
  因此本模块的写入按"发报文 -> 回读校验 -> 如实标注是否真正生效"实现，
  绝不把 code=0 当成写入成功。

链路（复用现有 LINK TCP 通道，帧格式 Topic/Length/JSON）：
    remoteconfig/edit        打开设备编辑开关（顶层字段 driver_name/enable）
    device/list/get          写入前基线
    device/add               每台 HA 设备建一个物理模型
    device/list/get          回读差分取自动生成的 oid（add_reply 不返回 oid）
    function/attribute/add   每个功能一个 sid = oid + 12 位序号
    function/list/get        回读校验 sid 是否真的出现
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    SPK_ATTRIBUTE_SCHEMA,
    STORAGE_KEY,
    STORAGE_VERSION,
    TOPIC_DEVICE_ADD,
    TOPIC_DEVICE_LIST,
    TOPIC_FUNCTION_ADD,
    TOPIC_FUNCTION_LIST,
    TOPIC_REMOTE_CONFIG_EDIT,
)
from .protocol import LinkError

LOGGER = logging.getLogger(__name__)

_SUCCESS = ("0", "200", 0, 200)


def _ok(resp: dict[str, Any] | None) -> bool:
    if not resp:
        return False
    return resp.get("code") in _SUCCESS


def _objs(resp: dict[str, Any] | None) -> Any:
    if not resp:
        return []
    objs = resp.get("objects")
    if isinstance(objs, dict):
        objs = objs.get("objects") or []
    return objs if isinstance(objs, list) else []


async def _device_oids(gateway: Any) -> set[str]:
    resp = await gateway.client.async_request(TOPIC_DEVICE_LIST.format(gw=gateway.oid), timeout=12)
    return {str(d.get("oid")) for d in _objs(resp) if isinstance(d, dict) and d.get("oid")}


async def _function_sids(gateway: Any) -> set[str]:
    resp = await gateway.client.async_request(TOPIC_FUNCTION_LIST.format(gw=gateway.oid), timeout=15)
    return {str(f.get("sid")) for f in _objs(resp) if isinstance(f, dict) and f.get("sid")}


def _attr_payload(spk: str, provided: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """按 spk 生成 attributes；实测网关用 data_type=boolean/number/string + value 枚举。"""
    schema = provided or SPK_ATTRIBUTE_SCHEMA.get(spk, [])
    out: list[dict[str, Any]] = []
    for item in schema:
        key = item.get("key")
        if not key:
            continue
        dtype = str(item.get("data_type") or "string")
        entry: dict[str, Any] = {"key": key, "data_type": dtype}
        if dtype == "boolean":
            entry["value"] = ["true", "false"]
        elif "min" in item or "max" in item:
            entry["min"] = str(item.get("min", "0"))
            entry["max"] = str(item.get("max", "100"))
        out.append(entry)
    return out


async def async_provision(
    hass: HomeAssistant,
    coordinator: Any,
    selection: dict[str, Any],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """按勾选结果写入网关，返回计划、逐项状态与错误。

    selection: {"devices": [{"device_id","name","model","functions":[
        {"entity_id","name","spk","attributes":[...]}]}]}
    """
    gateway = coordinator.gateway
    gw = gateway.oid
    devices_in = selection.get("devices") or []

    plan: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not devices_in:
        return {"dry_run": dry_run, "plan": [], "errors": ["没有勾选任何功能"], "warnings": []}

    # ---------------- 基线（演练也要读，用于判断"网关是否真的接受"）
    try:
        oids_before = await _device_oids(gateway)
    except LinkError as err:
        return {
            "dry_run": dry_run,
            "plan": [],
            "errors": [f"读取网关设备列表失败（网关不可达？）：{err}"],
            "warnings": [],
        }

    if not dry_run:
        try:
            sids_before = await _function_sids(gateway)
        except LinkError:
            sids_before = set()

        # 打开设备编辑开关（文档：driver_name=buspro/knx，enable=OID 列表或 enable/disable）
        for driver in ("buspro", "a"):
            try:
                resp = await gateway.client.async_request(
                    TOPIC_REMOTE_CONFIG_EDIT.format(gw=gw),
                    {"driver_name": driver, "enable": "enable"},
                    timeout=6,
                )
                if _ok(resp):
                    break
            except LinkError as err:
                LOGGER.debug("remoteconfig/edit(%s) 无响应: %s", driver, err)
    else:
        sids_before = set()

    seq = 0
    for device in devices_in:
        seq += 1
        dev_name = device.get("name") or device.get("device_id") or f"HA-{seq}"
        oid = ""
        dev_entry: dict[str, Any] = {
            "device_id": device.get("device_id"),
            "name": dev_name,
            "oid": "",
            "applied": False,
            "functions": [],
        }

        # ------------------------------------------------ 新增物理模型
        if dry_run:
            oid = f"<new-device-oid>"
            dev_entry["oid"] = oid
        else:
            body = {
                "objects": [
                    {
                        "device_name": dev_name,
                        "device_model": (device.get("model") or "HA-DEVICE")[:32],
                        "device_mac": "000000000000",
                        "addresses": "0a00",
                        "driver_code": "a",
                        "src": "a",
                    }
                ]
            }
            try:
                resp = await gateway.client.async_request(
                    TOPIC_DEVICE_ADD.format(gw=gw), body, timeout=12
                )
            except LinkError as err:
                errors.append(f"设备「{dev_name}」新增超时: {err}")
                plan.append(dev_entry)
                continue

            if not _ok(resp):
                errors.append(f"设备「{dev_name}」新增被拒绝: {resp}")
                plan.append(dev_entry)
                continue

            # 回读差分：add_reply 不返回 oid，只能靠设备列表差分
            try:
                oids_after = await _device_oids(gateway)
            except LinkError:
                oids_after = set(oids_before)
            new_oids = sorted(oids_after - oids_before)
            if new_oids:
                oid = new_oids[-1]
                oids_before.add(oid)
                dev_entry["applied"] = True
            else:
                # code=0 但设备列表没变 —— 明确标注未生效，不假装成功
                warnings.append(
                    f"设备「{dev_name}」device/add 返回 Success，但网关设备列表未新增，"
                    "判定为未生效（设备编辑需走 MQTT 远端通道）"
                )
                dev_entry["applied"] = False
            dev_entry["oid"] = oid

        # ------------------------------------------------ 逐功能点
        for idx, func in enumerate(device.get("functions") or [], start=1):
            spk = (func.get("spk") or "").strip()
            func_name = func.get("name") or func.get("entity_id")
            if not spk:
                warnings.append(f"「{dev_name}」跳过功能「{func_name}」：未选择 HDL 功能类型")
                continue

            sid = f"{oid}{idx:012d}" if oid else ""

            item = {
                "entity_id": func.get("entity_id"),
                "name": func_name,
                "spk": spk,
                "sid": sid,
                "ok": False,
            }

            if dry_run:
                item["ok"] = True
                item["note"] = "演练：未写网关"
                dev_entry["functions"].append(item)
                continue

            if not dev_entry["applied"]:
                item["note"] = "设备未建立，功能未下发"
                dev_entry["functions"].append(item)
                continue

            if sid in sids_before:
                item["note"] = "sid 已存在，跳过"
                dev_entry["functions"].append(item)
                continue

            body = {
                "objects": [
                    {
                        "oid": oid,
                        "sid": sid,
                        "name": str(func_name)[:32],
                        "spk": spk,
                        "omodel": (device.get("model") or "HA-DEVICE")[:32],
                        "online": "true",
                        "attributes": _attr_payload(spk, func.get("attributes")),
                    }
                ]
            }
            try:
                resp = await gateway.client.async_request(
                    TOPIC_FUNCTION_ADD.format(gw=gw), body, timeout=12
                )
                if not _ok(resp):
                    item["note"] = f"网关拒绝: {resp}"
                    errors.append(f"功能「{func_name}」新增失败: {resp}")
                else:
                    # 回读校验
                    try:
                        sids_after = await _function_sids(gateway)
                    except LinkError:
                        sids_after = sids_before
                    if sid in sids_after:
                        item["ok"] = True
                        item["note"] = "回读确认"
                        sids_before.add(sid)
                    else:
                        item["note"] = "返回 Success 但功能列表未出现（未生效）"
                        warnings.append(f"功能「{func_name}」未真正写入网关")
            except LinkError as err:
                item["note"] = str(err)[:200]
                errors.append(f"功能「{func_name}」新增超时: {err}")
            dev_entry["functions"].append(item)

        plan.append(dev_entry)

    if not dry_run:
        await _async_save_mapping(hass, plan)

    return {
        "dry_run": dry_run,
        "plan": plan,
        "errors": errors,
        "warnings": warnings,
        "created_devices": sum(1 for p in plan if p.get("applied")),
        "created_functions": sum(1 for p in plan for f in p["functions"] if f.get("ok")),
    }


async def _async_save_mapping(hass: HomeAssistant, plan: list[dict[str, Any]]) -> None:
    """把 entity_id -> (oid, sid) 落盘，供后续核对与删除。"""
    try:
        from homeassistant.helpers.storage import Store

        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        data = await store.async_load() or {}
        mapping = data.get("mapping", {}) if isinstance(data, dict) else {}
        for dev in plan:
            for func in dev.get("functions") or []:
                if func.get("entity_id") and func.get("ok"):
                    mapping[func["entity_id"]] = {
                        "oid": dev.get("oid"),
                        "sid": func.get("sid"),
                        "spk": func.get("spk"),
                        "device_id": dev.get("device_id"),
                        "name": func.get("name"),
                    }
        await store.async_save({"mapping": mapping})
        LOGGER.info("已保存 %d 条 entity→sid 映射", len(mapping))
    except Exception as err:  # noqa: BLE001 - 落盘失败不能影响下发结果
        LOGGER.warning("保存映射失败: %s", err)


async def async_mapping(hass: HomeAssistant) -> dict[str, Any]:
    """读取已保存的映射。"""
    try:
        from homeassistant.helpers.storage import Store

        store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        data = await store.async_load() or {}
        return data.get("mapping", {}) if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
