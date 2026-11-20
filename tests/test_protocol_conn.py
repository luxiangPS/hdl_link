"""连接生命周期自测：并发 async_connect 只能有一条 reader。

回归场景（HA 真实日志里出现过）：
    RuntimeError: read() called while another coroutine is already waiting
这是同一 StreamReader 上开了两条读协程导致的。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "hdl_link"))
import protocol  # noqa: E402

FAILED = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILED.append(name)


async def main():
    received = []
    accepted = []

    async def handler(reader, writer):
        accepted.append(1)
        # 只推一帧普通明文上报，然后保持连接
        writer.write(protocol.encode_frame(
            "/base/TESTGW/thing/property/up",
            {"objects": [{"sid": "AAA", "status": [{"key": "on_off", "value": "on"}]}]},
        ))
        await writer.drain()
        # 阻塞到客户端断开，测试结束时不会留下悬挂的 server handler
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = protocol.LinkClient("127.0.0.1", port, local_secret=None)
    client.push_handler = lambda objs: received.append(objs)

    print("[1] 并发连接 5 次")
    results = await asyncio.gather(
        *[client.async_connect() for _ in range(5)], return_exceptions=True
    )
    check("没有异常抛出", all(not isinstance(r, Exception) for r in results))
    check("服务端只被连了 1 次", len(accepted) == 1)

    print("[2] 上报能被收到（说明 reader 活着且只有一条）")
    await asyncio.sleep(0.5)
    check("收到 1 帧上报", len(received) == 1 and received[0][0]["sid"] == "AAA")

    print("[3] 重复 close 再连")
    await client.async_close()
    await client.async_close()
    await client.async_connect()
    await asyncio.sleep(0.5)
    check("重连后仍能接收", len(received) == 2)

    print("[4] 断线后 self 状态被清干净")
    await client.async_close()
    check("connected=False", client.connected is False)
    check("reader 为 None", client._reader is None)

    server.close()
    await server.wait_closed()


asyncio.run(main())
print("\n结果：%d 项失败" % len(FAILED))
sys.exit(1 if FAILED else 0)
