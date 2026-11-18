"""网关发现：LINK 广播搜索 + 本机网段并发扫描（用户不必填写 IP）。"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

from .const import DEFAULT_PORT, SCAN_CONCURRENCY, SCAN_TIMEOUT
from .gateway import GatewayInfo, HdlGateway

LOGGER = logging.getLogger(__name__)


def local_ipv4_networks() -> list[ipaddress.IPv4Interface]:
    """枚举本机所有可用的 IPv4 网段（不做 DNS、不联网）。"""
    nets: list[ipaddress.IPv4Interface] = []
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        candidates = {info[4][0] for info in infos}
    except OSError:
        candidates = set()

    # 兜底：UDP connect 到外网地址以取得默认出口源地址（不发包）
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
        if ip.is_loopback or ip.is_link_local:
            continue
        if ip.is_private:
            nets.append(ipaddress.IPv4Interface(f"{ip}/24"))
    return nets


def _hosts_in(net: ipaddress.IPv4Interface, limit: int = 254) -> list[str]:
    hosts = list(net.network.hosts())[:limit]
    return [str(h) for h in hosts]


async def _probe(host: str, port: int, timeout: float) -> GatewayInfo | None:
    try:
        return await HdlGateway.async_search(host, port, timeout)
    except Exception:  # noqa: BLE001 - 探测失败即跳过
        return None


async def async_scan(
    extra_hosts: list[str] | None = None,
    *,
    timeout: float = 12.0,
    port: int = DEFAULT_PORT,
) -> list[GatewayInfo]:
    """扫描本机各网段的地址（并发，总时限 timeout 秒），返回应答的 HDL 网关。

    extra_hosts 非空时只探测这些地址，不再全段扫描。
    """
    hosts: set[str] = set(extra_hosts or [])
    if not hosts:
        for net in local_ipv4_networks():
            hosts.update(_hosts_in(net))
    if not hosts:
        return []

    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    results: list[GatewayInfo] = []

    async def guarded(host: str) -> None:
        async with sem:
            info = await _probe(host, port, SCAN_TIMEOUT)
            if info is not None and info.oid:
                if all(info.oid != x.oid for x in results):
                    results.append(info)

    tasks = [asyncio.create_task(guarded(h)) for h in hosts]
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    LOGGER.debug("扫描 %d 个地址，发现 %d 个 HDL 网关", len(hosts), len(results))
    return results
