"""场景桥：把 HA 实体映射成网关里的一对「开/关」场景。

为什么需要它
------------
HDL 网关（实测 MGWIP.430 / fw V03.11.19）对「三方 LINK 设备」只做两件事：
收 ``property/up``（更新功能点状态）、回 ``property/down_reply``，
**不会**把项目/面板下发的控制帧转发给设备本身。所以虚拟设备的 ``property/down``
这条路在网关内部就断了，热控不了。

网关会广播的是「场景执行」：``/user/{gw}/custom/scene/status/up``。
因此把 HA 实体做成网关里的两条空动作场景（"…开" / "…关"），在 Studio / App /
面板里点这两条场景，网关广播事件，HA 侧按绑定执行 turn_on / turn_off。
整条链路只用开放协议，不需要在网关装驱动。

场景 sid 规则（实测）
----------------------
14 字节十六进制：``020103`` + 6 字节设备段 + 5 字节序号段。

- 设备段取实体 id 的 md5 前 6 字节，保证同一实体稳定；
- **开/关的区别必须放在序号段**（``01 0001 0001`` / ``01 0002 0001``）。
  实测：同一对场景如果只在设备段上不同、序号段相同，后建的会把先建的顶掉；
  序号段不同则两条并存。
"""

from __future__ import annotations

import hashlib

SCENE_PREFIX = "020103"
SCENE_TAIL = "0001"


def scene_sids(entity_id: str) -> tuple[str, str]:
    """返回 (开场景 sid, 关场景 sid)，同一实体永远一致。"""
    digest = hashlib.md5(entity_id.encode("utf-8")).hexdigest()[:12].upper()
    base = f"{SCENE_PREFIX}{digest}01"
    return f"{base}0001{SCENE_TAIL}", f"{base}0002{SCENE_TAIL}"
