"""网关发现：LINK 本地搜索（UDP 广播），必要时回退 TCP 单播探测。

用户不需要填写网关 IP 或总线地址：集成向局域网广播搜索请求，
网关应答自身信息（型号、名称、地址、是否主网关等），由用户从列表中挑一台接入。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time

from .const import DEFAULT_PORT, SCAN_CONCURRENCY, SCAN_TIMEOUT, TOPIC_GATEWAY_SEARCH, UDP_SEARCH_PORT
from .gateway import GatewayInfo, HdlGateway

LOGGER = logging.getLogger(__name__)

_FRAME = re.compile(rb"Topic:(.*?)\r\nLength:\s*(\d+)\r\n\r\n")
_BROADCASTS = ("255.255.255.255",)


def local_ipv4_networks() -> list[ipaddress.IPv4Interface]:
    """枚举本机所有可用的 IPv4 网段（不做 DNS、不联网）。"""
    nets: list[ipaddress.IPv4Interface] = []
    candidates: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass

    # 兜底：UDP connect 到外部地址以取得默认出口源地址（不发包）
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("10.255.255.255", 1))
        candidates.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    for addr in candidates:
        try:
            ip = ipaddress.IPv4Address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or not ip.is_private:
            continue
        nets.append(ipaddress.IPv4Interface(f"{ip}/24"))
    return nets


def _broadcast_targets() -> list[str]:
    targets = list(_BROADCASTS)
    for net in local_ipv4_networks():
        bcast = str(net.network.broadcast_address)
        if bcast not in targets:
            targets.append(bcast)
    return targets


def _search_frame() -> bytes:
    body = {"id": str(int(time.time() * 1000) % 2147483647), "oids": [], "time_stamp": str(int(time.time() * 1000))}
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    head = "Topic:%s\r\nLength: %d\r\n\r\n" % (TOPIC_GATEWAY_SEARCH, len(raw))
    return head.encode("utf-8") + raw


def _parse_reply(payload: bytes) -> dict | None:
    match = _FRAME.match(payload)
    if match is None or not match.group(1).decode("utf-8", "ignore").endswith("search_reply"):
        return None
    size = int(match.group(2))
    try:
        body = json.loads(payload[match.end():match.end() + size])
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _to_info(obj: dict) -> GatewayInfo | None:
    host = str(obj.get("ip_address") or "").strip()
    if not host:
        return None
    return GatewayInfo(
        host=host,
        oid=str(obj.get("oid") or obj.get("gatewayId") or ""),
        name=str(obj.get("device_name") or obj.get("device_model") or host),
        model=str(obj.get("device_model") or ""),
        mac=str(obj.get("device_mac") or ""),
        gateway_id=str(obj.get("gatewayId") or ""),
        raw=obj,
    )


def _collect(sock_targets: list[str], timeout: float, port: int, rounds: int = 2) -> list[GatewayInfo]:
    """向给定地址逐个发送搜索帧并收集应答（同步实现，供 executor 调用）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # 网关把应答发回搜索端口，因此必须先占住该端口
        sock.bind(("", port))
    except OSError as err:
        LOGGER.debug("无法绑定 UDP %s（%s），改用临时端口", port, err)
        try:
            sock.bind(("", 0))
        except OSError:
            sock.close()
            return []

    frame = _search_frame()
    for _ in range(rounds):
        for target in sock_targets:
            try:
                sock.sendto(frame, (target, port))
            except OSError as err:
                LOGGER.debug("发送 %s:%s 失败: %s", target, port, err)
        time.sleep(0.12)

    sock.settimeout(0.35)
    found: list[GatewayInfo] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, _addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            break
        body = _parse_reply(data)
        if body is None:
            continue
        objects = body.get("objects")
        items = objects if isinstance(objects, list) else [objects or {}]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            info = _to_info(obj)
            if info is None or not info.oid:
                continue
            if all(info.oid != x.oid for x in found):
                found.append(info)
    sock.close()
    return found


def udp_search(timeout: float = 3.0, port: int = UDP_SEARCH_PORT) -> list[GatewayInfo]:
    """向局域网广播搜索请求并收集应答（同步实现，供 executor 调用）。"""
    found = _collect(_broadcast_targets(), timeout, port, rounds=2)
    LOGGER.debug("广播搜索到 %d 台网关", len(found))
    return found


def udp_unicast_search(
    hosts: list[str], timeout: float = 3.0, port: int = UDP_SEARCH_PORT
) -> list[GatewayInfo]:
    """逐个地址单播搜索。

    广播穿不过虚拟化 NAT（HA 在 NAT 网段时广播只在 NAT 内部打转），
    但单播 UDP 可以经 NAT 出站并把应答收回来，因此在跨网段部署时用它兜底。
    """
    if not hosts:
        return []
    found = _collect(hosts, timeout, port, rounds=2)
    LOGGER.debug("单播搜索 %d 个地址，命中 %d 台网关", len(hosts), len(found))
    return found


async def _probe(host: str, port: int, timeout: float) -> GatewayInfo | None:
    try:
        return await HdlGateway.async_search(host, port, timeout)
    except Exception:  # noqa: BLE001 - 探测失败即跳过
        return None


async def _tcp_scan(hosts: list[str], timeout: float, port: int) -> list[GatewayInfo]:
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    results: list[GatewayInfo] = []

    async def guarded(host: str) -> None:
        async with sem:
            info = await _probe(host, port, SCAN_TIMEOUT)
            if info is not None and info.oid and all(info.oid != x.oid for x in results):
                results.append(info)

    tasks = [asyncio.create_task(guarded(h)) for h in hosts]
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    return results


def expand_hosts(value: str) -> tuple[list[str], bool]:
    """把 '192.168.1.150' / 'gw.local' / '192.168.1.0/24' 展开成待探测主机列表。

    返回 (主机列表, 是否为网段)。网段超过 1024 个地址视为非法，避免误扫大网。
    """
    text = (value or "").strip()
    if not text:
        return [], False
    if "/" in text:
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError:
            return [], True
        if net.num_addresses > 1024:
            return [], True
        return [str(h) for h in net.hosts()], True
    try:
        ipaddress.ip_address(text)
        return [text], False
    except ValueError:
        if re.match(r"^[A-Za-z0-9._-]{1,63}$", text):
            return [text], False
        return [], False


async def async_scan(
    extra_hosts: list[str] | None = None,
    *,
    timeout: float = 8.0,
    port: int = DEFAULT_PORT,
) -> list[GatewayInfo]:
    """发现网关。

    · 未指定地址时：用 LINK 本地搜索（UDP 广播）拿网关信息；无应答再回退 TCP 单播扫网段。
    · 指定了地址（手动输入/校验）时：只对这些地址做 TCP 单播探测。
    """
    if extra_hosts:
        hosts = list(dict.fromkeys(extra_hosts))
        loop = asyncio.get_running_loop()
        try:
            found = await loop.run_in_executor(None, udp_unicast_search, hosts, min(timeout, 6.0))
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("UDP 单播搜索异常: %s", err)
            found = []
        if found:
            return found
        return await _tcp_scan(hosts, timeout, port)

    loop = asyncio.get_running_loop()
    try:
        found = await loop.run_in_executor(None, udp_search, min(timeout, 4.0), UDP_SEARCH_PORT)
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("UDP 本地搜索异常: %s", err)
        found = []
    if found:
        return found

    hosts: set[str] = set()
    for net in local_ipv4_networks():
        hosts.update(str(h) for h in list(net.network.hosts())[:254])
    if not hosts:
        return []
    # 广播被 NAT/路由器挡住时，先对本机网段做 UDP 单播，比 TCP 扫描快得多
    try:
        found = await loop.run_in_executor(None, udp_unicast_search, sorted(hosts), min(timeout, 6.0))
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("UDP 网段单播异常: %s", err)
        found = []
    if found:
        return found

    LOGGER.debug("广播与单播均无应答，回退 TCP 扫描 %d 个地址", len(hosts))
    return await _tcp_scan(sorted(hosts), timeout, port)
