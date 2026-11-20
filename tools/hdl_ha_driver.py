#!/usr/bin/env python3
"""HA 驱动（网关侧代理，局域网任意一台机器都能跑）。

职责：
  1. 连 HDL 网关 8586（LINK 协议）；
  2. 用 driverconfig/edit 以 driver_name 上报 HA 设备清单（驱动已安装才成功）；
  3. 收到网关下发的 property/down → POST 给 HA 的 /api/hdl_link/driver/down 执行；
  4. 定时把 HA 设备状态用 property/up 回报，维持在线。

依赖：仅标准库。HA 侧需要长令牌（创建集成时生成的长效访问令牌）。

用法：
  set HA_URL=http://172.26.30.104:8123
  set HA_TOKEN=eyJ...
  python hdl_ha_driver.py --gateway 192.168.1.151 --name ha

注意：网关实测会校验驱动是否已安装（driverconfig/edit 返回 500001「驱动不存在」时，
说明这个 driver_name 还没在网关上启用；此时脚本只能跑通状态上报，收不到下行）。
"""
import argparse
import json
import socket
import time
import urllib.request

FRAME_HEADER = "Topic:%s\r\nLength: %d\r\n\r\n"


class Link:
    def __init__(self, host, port=8586):
        self.sock = socket.create_connection((host, port), timeout=8)
        self.buf = b""
        self.seq = 0

    def send(self, topic, body):
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.sock.sendall((FRAME_HEADER % (topic, len(payload))).encode() + payload)

    def request(self, topic, body, timeout=8):
        self.seq += 1
        body = dict(body or {})
        body["id"] = str(self.seq)
        body.setdefault("time_stamp", str(int(time.time() * 1000)))
        self.send(topic, body)
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.2, end - time.time()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk
            for topic_name, raw in self.split_frames():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                if str(parsed.get("id")) == body["id"]:
                    return parsed
        return None

    def split_frames(self):
        frames = []
        while True:
            i = self.buf.find(b"Topic:")
            if i < 0:
                return frames
            j = self.buf.find(b"\r\nLength:", i)
            k = self.buf.find(b"\r\n\r\n", j)
            if j < 0 or k < 0:
                return frames
            try:
                n = int(self.buf[j + 9:k].strip())
            except ValueError:
                self.buf = self.buf[i + 6:]
                continue
            if len(self.buf) < k + 4 + n:
                return frames
            topic = self.buf[i + 6:j].decode("utf-8", "ignore")
            raw = self.buf[k + 4:k + 4 + n]
            self.buf = self.buf[k + 4 + n:]
            frames.append((topic, raw))

    def read_frames(self, seconds=10):
        """持续读取主动下推帧，返回 (topic, json) 列表。"""
        out = []
        end = time.time() + seconds
        while time.time() < end:
            self.sock.settimeout(max(0.2, end - time.time()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            self.buf += chunk
            for topic, raw in self.split_frames():
                try:
                    out.append((topic, json.loads(raw)))
                except ValueError:
                    continue
        return out


def ha_get(url, token, path):
    req = urllib.request.Request(
        url.rstrip("/") + path,
        headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def ha_post(url, token, path, body):
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=data,
        headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except Exception as err:  # noqa: BLE001
        return {"error": str(err)}


def build_driver_objects(device_list):
    """把 HA 清单转成驱动配置表 objects。"""
    mapping = device_list.get("mapping") or {}
    objects = []
    for entity_id, info in mapping.items():
        objects.append({
            "src": "ha",
            "spk": info.get("spk"),
            "sid": info.get("sid"),
            "name": info.get("name") or entity_id,
            "type": "normal",
            "attrs": [{"name": "on_off", "type": 1}],
        })
    return objects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="192.168.1.151", help="网关卡 IP")
    ap.add_argument("--port", type=int, default=8586)
    ap.add_argument("--gw-id", default="", help="网关 oid，留空则从 HA 清单里取")
    ap.add_argument("--name", default="ha", help="driver_name")
    ap.add_argument("--ha-url", default="http://127.0.0.1:8123")
    ap.add_argument("--ha-token", default="")
    ap.add_argument("--interval", type=int, default=10, help="状态回报间隔（秒）")
    args = ap.parse_args()

    if not args.ha_token:
        raise SystemExit("需要 --ha-token（HA 长效访问令牌）")

    link = Link(args.gateway, args.port)
    print("[driver] 已连接网关 %s:%s" % (args.gateway, args.port))

    last_push = 0
    while True:
        # 1) 拉 HA 清单
        try:
            lst = ha_get(args.ha_url, args.ha_token, "/api/hdl_link/driver/devices")
        except Exception as err:  # noqa: BLE001
            print("[driver] 取 HA 清单失败:", err)
            time.sleep(5)
            continue

        gw = args.gw_id or ""
        if not gw:
            mapping = lst.get("mapping") or {}
            # oid 是网关侧派生的，取映射里最长公共前缀不现实；这里读一次网关设备列表反查
            resp = link.request("/user/all/custom/gateway/search", {})
            gw = (resp or {}).get("oid") or ""
        if not gw:
            print("[driver] 还没拿到网关 oid，稍后重试")
            time.sleep(5)
            continue

        # 2) 上报驱动配置表（验证驱动是否存在）
        objects = build_driver_objects(lst)
        resp = link.request(
            "/user/%s/custom/driverconfig/edit" % gw,
            {"driver_name": args.name, "objects": objects},
        )
        code = (resp or {}).get("code")
        print("[driver] driverconfig/edit -> code=%s msg=%s objects=%d"
              % (code, (resp or {}).get("msg"), len(objects)))

        # 3) 状态回报 + 收下行
        deadline = time.time() + args.interval
        while time.time() < deadline:
            frames = link.read_frames(2)
            for topic, body in frames:
                objects_in = body.get("objects") if isinstance(body, dict) else None
                if not objects_in or "down" not in topic:
                    continue
                print("[driver] 收到下行 %s: %s" % (topic, json.dumps(objects_in, ensure_ascii=False)[:200]))
                ha_post(args.ha_url, args.ha_token, "/api/hdl_link/driver/down", {"objects": objects_in})

        # 4) 定期把状态推回（维持在线）
        if time.time() - last_push >= args.interval:
            last_push = time.time()
            for item in (lst.get("mapping") or {}).values():
                sid = item.get("sid")
                if not sid:
                    continue
                link.send("/base/%s/thing/property/up" % gw,
                          {"objects": [{"sid": sid, "status": [{"key": "on_off", "value": "on"}]}]})


if __name__ == "__main__":
    main()
