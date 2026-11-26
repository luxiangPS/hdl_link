"""场景桥：把 HA 实体映射成网关里的一对「开 / 关」场景。

为什么需要它
------------
HDL 网关对「三方设备」只处理状态上报与控制回执，不会把资料项目 / 面板下发的
控制转发给设备本身，所以「收到控制就执行」这条路走不通。

网关会广播的是「场景执行」事件。因此把 HA 实体做成网关里的两条空动作场景
（"…开" / "…关"），在 Studio / App / 面板里点这两条场景，网关广播事件，
HA 侧按绑定执行 turn_on / turn_off。整条链路不需要在网关装驱动。

场景标识规则（实测）
--------------------
标识由实体 id 稳定派生，同一实体每次生成的都一致。
**开 / 关的区别必须放在序号段**：若两条只在设备段上不同、序号段相同，
后建的会把先建的顶掉；序号段不同则两条并存。
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
