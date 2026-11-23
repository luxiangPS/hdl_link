"""HDL 网关驱动检测与本地安装（hex 文件传输协议，TCP 8586）。

设计目标：
- **不使用任何账号密码**：这条通道是 Studio 同款私有协议，只认字节流。
- **本地文件安装**：安装包随集成一起分发（`driver_pkg/ha_driver.zip`），不依赖云端 URL。
- **安装即生效**：网关 LINK 解包后会自己 `install.sh` + 启动驱动，无需重启 LINK。

协议（来自 HDL Studio `GateWay.cs` 的 `DriverSetup/DriverSendPacket`）：
- 通用头：`hex` + [3]=1 [4]=1 + [5]=category + [6]=cmd + [7..10]=大端(payload+4 长度)
- `0x01` 读驱动列表 → 回 `0x02`，正文从 [12] 起为 `名称\\r\\n版本\\r\\n代码\\r\\n` 块
- `0x03` 安装申请 → payload `<包名>.zip\\r\\n`，尾 4 字节大端=包体总长度
- 回 `0x04`：`[11..12]` 总包数、`[13]` 单包 KB
- 回 `0x05`：`[11..12]` = 请求的包序号；若为 `success`/`failed` 文本则结束
- `0x06` 数据包：`[11..12]` 包序号、`[13..16]` 大端本包长度、`[17..]` 数据
- `0x09` 卸载：payload = 驱动 **代码**（如 `00898978\\r\\n`）→ 回 `0x0A`

注意：ZIP 里必须带 **目录条目**（如 `HA/`、`HA/config/`），否则网关会报
「解压失败，没有驱动根目录」——这是实测踩出来的硬约束。
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import zipfile
from pathlib import Path
from typing import Callable

LOGGER = logging.getLogger(__name__)

DRIVER_OTA_PORT = 8586
HA_DRIVER_NAME = "HA"
HA_DRIVER_CODE = "00898978"

PKG_DIR = Path(__file__).parent / "driver_pkg"
HA_DRIVER_PKG = PKG_DIR / "LD_MGWIP.430_V01.02.00_261123_RK3308_HA.zip"

MAX_PACKET_TIMEOUT = 90


def _head(cmd: int, total: int, category: int = 1) -> bytes:
    return b"hex" + bytes([1, 1, category, cmd]) + struct.pack(">I", total)


def _split_frames(buf: bytes) -> tuple[list[tuple[str, bytes]], bytes]:
    """把混在一起的 hex 二进制帧与 Topic JSON 帧分开。"""
    frames: list[tuple[str, bytes]] = []
    while buf:
        if buf.startswith(b"hex"):
            if len(buf) < 11:
                break
            need = 11 + struct.unpack(">I", buf[7:11])[0]
            if len(buf) < need:
                break
            frames.append(("hex", buf[:need]))
            buf = buf[need:]
        elif buf.startswith(b"Topic:"):
            pos = buf.find(b"\r\n\r\n")
            if pos < 0:
                break
            length = 0
            for line in buf[:pos].split(b"\r\n"):
                if line.lower().startswith(b"length:"):
                    length = int(line.split(b":", 1)[1].strip())
            if len(buf) < pos + 4 + length:
                break
            frames.append(("topic", buf[: pos + 4 + length]))
            buf = buf[pos + 4 + length:]
        else:
            candidates = [i for i in (buf.find(b"hex"), buf.find(b"Topic:")) if i > 0]
            buf = buf[min(candidates)] if candidates else b""
    return frames, buf


async def async_list_drivers(host: str, port: int = DRIVER_OTA_PORT, timeout: float = 5.0) -> list[dict]:
    """读网关驱动列表；返回 [{"name","version","code"}]。失败抛异常由调用方兜底。"""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(_head(0x01, 4) + struct.pack(">I", 0))
        await writer.drain()
        buf = b""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
            frames, buf = _split_frames(buf)
            for kind, frame in frames:
                if kind == "hex" and frame[6] == 0x02:
                    return _parse_driver_list(frame[12:-2])
        raise TimeoutError("网关未返回驱动列表")
    finally:
        writer.close()


def _parse_driver_list(body: bytes) -> list[dict]:
    drivers: list[dict] = []
    for block in body.split(b"\r\n\r\n"):
        lines = [x for x in block.split(b"\r\n") if x]
        if len(lines) < 3:
            continue
        drivers.append(
            {
                "name": lines[0].decode("utf-8", "replace"),
                "version": lines[1].decode("utf-8", "replace"),
                "code": lines[2].decode("utf-8", "replace"),
            }
        )
    return drivers


async def async_install_driver(
    host: str,
    zip_path: str | Path,
    port: int = DRIVER_OTA_PORT,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """把一个本地 zip 装进网关；成功返回 True。"""
    log = progress or (lambda msg: LOGGER.debug("驱动安装: %s", msg))
    zip_path = Path(zip_path)
    blob = zip_path.read_bytes()
    payload = (zip_path.name + "\r\n").encode("utf-8")

    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(_head(0x03, len(payload) + 4) + payload + struct.pack(">I", len(blob)))
        await writer.drain()
        log(f"申请安装 {zip_path.name}（{len(blob)} 字节）")

        total = 0
        kb = 0
        sent = 0
        buf = b""
        deadline = asyncio.get_running_loop().time() + MAX_PACKET_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
            frames, buf = _split_frames(buf)
            for kind, frame in frames:
                if kind == "topic":
                    desc = _progress_desc(frame)
                    if desc:
                        log(f"进度 {desc}")
                        if "失败" in desc:
                            return False
                    continue
                cmd = frame[6]
                tail = frame[11:]
                if cmd == 0x04:
                    total = tail[0] * 256 + tail[1]
                    kb = tail[2] if len(tail) > 2 else 0
                    log(f"网关就绪：{total} 包 / {kb}KB")
                elif cmd == 0x05:
                    text = tail[:16].decode("latin-1", "replace")
                    if text.startswith("success"):
                        log("网关回执 success")
                        return True
                    if text.startswith("failed"):
                        log("网关回执 failed")
                        return False
                    idx = tail[0] * 256 + tail[1]
                    if total and idx <= total and idx > sent:
                        size = kb * 1024
                        start = (idx - 1) * size
                        if idx == total:
                            size = len(blob) - start
                        data = blob[start : start + size]
                        writer.write(
                            _head(0x06, 2 + 4 + len(data))
                            + tail[0:2]
                            + struct.pack(">I", len(data))
                            + data
                        )
                        await writer.drain()
                        sent = idx
                        log(f"发送包 {idx}/{total}（{len(data)} 字节）")
        return False
    finally:
        writer.close()


def _progress_desc(frame: bytes) -> str | None:
    pos = frame.find(b"\r\n\r\n")
    if pos < 0:
        return None
    try:
        body = json.loads(frame[pos + 4 :].decode("utf-8", "replace"))
        obj = (body.get("objects") or [{}])[0]
    except Exception:  # noqa: BLE001 - 进度帧解析失败不影响安装
        return None
    desc = obj.get("desc")
    if not desc:
        return None
    return f"{obj.get('command', '')} {obj.get('step', '')}% {desc}"


def has_ha_driver(drivers: list[dict]) -> bool:
    for drv in drivers:
        if drv.get("name") == HA_DRIVER_NAME or drv.get("code") == HA_DRIVER_CODE:
            return True
    return False


def _patch_token(src: Path, dst: Path, token: str, base_url: str, gw_id: str) -> None:
    """把集成手里的令牌/地址写进安装包里的 config/ha_map.json（凭据不落代码）。"""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("config/ha_map.json"):
                try:
                    conf = json.loads(data.decode("utf-8"))
                    conf.setdefault("ha", {})
                    if token:
                        conf["ha"]["token"] = token
                    if base_url:
                        conf["ha"]["base_url"] = base_url
                        conf["ha"]["base"] = base_url
                    if gw_id:
                        conf["ha"]["gw_id"] = gw_id
                    data = json.dumps(conf, ensure_ascii=False, indent=2).encode("utf-8")
                except Exception:  # noqa: BLE001 - 配置格式异常时按原样打包
                    LOGGER.debug("ha_map.json 改写失败，按原样打包")
            zout.writestr(item, data)


async def async_ensure_ha_driver(
    host: str,
    gateway_oid: str,
    token: str = "",
    base_url: str = "",
    port: int = DRIVER_OTA_PORT,
    package: Path | None = None,
) -> dict:
    """确保网关上装了 HA 驱动；缺了就用本地包装进去。

    返回 {"present": bool, "installed": bool, "drivers": [...]}。
    """
    pkg = package or HA_DRIVER_PKG
    drivers = await async_list_drivers(host, port)
    if has_ha_driver(drivers):
        LOGGER.debug("网关已有 HA 驱动，跳过安装")
        return {"present": True, "installed": False, "drivers": drivers}

    if not pkg.exists():
        LOGGER.warning("缺少驱动安装包 %s，无法自动安装", pkg)
        return {"present": False, "installed": False, "drivers": drivers}

    LOGGER.info("网关未检测到 HA 驱动，开始自动安装：%s", pkg.name)
    tmp = pkg.with_name("ha_driver.tmp.zip")
    try:
        _patch_token(pkg, tmp, token, base_url, gateway_oid)
        ok = await async_install_driver(host, tmp, port)
    finally:
        tmp.unlink(missing_ok=True)

    drivers = await async_list_drivers(host, port)
    present = has_ha_driver(drivers)
    LOGGER.info("HA 驱动自动安装%s；当前驱动：%s", "成功" if ok and present else "失败",
                ", ".join(d["name"] for d in drivers))
    return {"present": present, "installed": ok and present, "drivers": drivers}
