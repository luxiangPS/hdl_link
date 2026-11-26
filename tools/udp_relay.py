"""接入阶段的中继——给 HA 跨网段 / NAT 环境兜底。

为什么需要：
    设备接入阶段是一次「一问一答」，而网关会把应答发回「请求来源地址的同端口」。
    如果 HA 跑在虚拟机上、经 NAT 出去，源端口会被改写，应答就再也到不了 HA，
    集成日志表现为「认证无响应」。

怎么用：
    在与网关同网段的机器上跑本脚本（Windows / Linux / 树莓派都行）：
        python udp_relay.py --gateway <网关IP> --ha <HA 的IP>
    脚本监听本机固定端口：
        - 收到 HA 发出的接入请求 → 原样转发给网关
        - 收到网关返回的应答 → 原样回灌给 HA
    HA 集成那边不用改任何配置，重试时就能收到应答。

注意：窗口需常开（或做成开机自启 / 服务）。接入成功后中继可以停掉；
网关重连或重启后若需要重新接入，再把它开起来。
"""

from __future__ import annotations

import argparse
import socket
import time

TOPIC_REQ = b"network_access/request\r\n"
TOPIC_REPLY = b"network_access/request_reply"


def main() -> None:
    ap = argparse.ArgumentParser(description="接入阶段的中继（UDP）")
    ap.add_argument("--gateway", default="192.168.1.151", help="网关 IP")
    ap.add_argument("--ha", default=None, help="HA 的 IP（不给则按收到的请求来源学习）")
    ap.add_argument("--port", type=int, default=8585, help="监听端口")
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
        except ConnectionResetError:
            # 对端（HA）已关闭对应端口，回包会触发 ICMP 端口不可达；
            # Windows 上表现为 10054，忽略后继续等下一次接入。
            print("对端已关闭端口（接入多半已完成），继续监听", flush=True)
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
