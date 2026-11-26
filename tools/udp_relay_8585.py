"""LINK 入网 UDP 中继（8585）——给 HA 跨网段/NAT 环境兜底。

为什么需要：
    LINK 的 A 设备入网靠 UDP 8585 一发一收。网关固定把
    `network_access/request_reply` 发到「请求来源 IP 的 8585 端口」。
    如果 HA 跑在虚拟机上、经 Hyper-V Default Switch NAT 出去，源端口会被
    改写（如 8585 → 49863），网关的回包就再也到不了 HA，集成日志表现为
    「入网认证无响应」。

怎么用：
    在**与网关同网段的机器**上跑本脚本（Windows / Linux / 树莓派都行）：
        python udp_relay_8585.py --gateway 192.168.1.151 --ha 172.26.30.104
    脚本监听本机 8585：
        - 收到 HA 发出的 network_access/request（HA 的广播副本会落到同网段机器）
          → 原样转发给网关
        - 收到网关回的 request_reply
          → 原样回灌给 HA 的 8585
    HA 集成那边不用改任何配置，重试时就能收到 reply。

注意：窗口需常开（或做成开机自启/服务）。入网成功后就只有 TCP 8586，中继
可以停掉；网关重连或重启后若需要重新入网，再把它开起来。
"""

from __future__ import annotations

import argparse
import socket
import time

TOPIC_REQ = b"network_access/request\r\n"
TOPIC_REPLY = b"network_access/request_reply"


def main() -> None:
    ap = argparse.ArgumentParser(description="LINK 入网 UDP 8585 中继")
    ap.add_argument("--gateway", default="192.168.1.151", help="网关 IP")
    ap.add_argument("--ha", default=None, help="HA 的 IP（不给则按收到的请求来源学习）")
    ap.add_argument("--port", type=int, default=8585, help="监听端口（默认 8585）")
    ap.add_argument("--seconds", type=int, default=0, help="运行时长，0 = 一直跑")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(2)
    print(f"relay listening :{args.port} -> gateway {args.gateway}:{args.port}", flush=True)

    ha = (args.ha, args.port) if args.ha else None
    end = time.time() + args.seconds if args.seconds else None

    while end is None or time.time() < end:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        head = data[:80]
        if TOPIC_REQ in head and addr[0] != args.gateway:
            ha = (addr[0], args.port)
            sock.sendto(data, (args.gateway, args.port))
            print(f"HA {addr} 入网请求 -> 网关", flush=True)
        elif TOPIC_REPLY in head and addr[0] == args.gateway:
            if ha:
                sock.sendto(data, ha)
                print(f"网关 reply -> HA {ha}", flush=True)
        else:
            print(f"忽略 {addr} {head[:40]!r}", flush=True)


if __name__ == "__main__":
    main()
