# 无驱动控制 HA：LINK 本地三方设备接入

目标：**不往网关装任何驱动/文件**，让 Studio / HDL App / 面板 能控制 HA 实体。

官方协议依据：《Link 协议接入》本地三方设备 / 本地三方应用对接方式
<https://iot-open-doc.hdlcontrol.com/docs/guides/device/local/link/overview>
（关键页：`device-onboarding`、`control-and-state`、`virtual-device`）。

## 为什么以前那条路走不通

3.0.0 之前试过的「本地应用截获 Studio 下行」走不通，原因是：

- `/base/{gw}/thing/property/down` **只点对点回发送者**，从不广播（双连接实测 26 秒 3 条 down，旁观连接收 0 条）；
- 没有驱动接管的功能点，网关不保存状态、指令回 `tag:"virtual"`；
- `driverconfig/edit` 回 `500001 驱动不存在`，没有接口能把设备塞进网关设备表。

## 官方给的替代路径：自己就是「三方设备」

```
Studio / APP / 面板
        │  property/down（点对点，只发给设备自己的 TCP 连接）
        ▼
   LINK 网关  ────────────────►  本进程（伪装 A 设备）  ──►  HA REST  ──►  实体
        ▲                              │
        └──── property/up（状态跟随） ──┘
```

七步协议（全部为官方文档口径）：

| 步骤 | 方向 | Topic |
|---|---|---|
| 1 网关通知入网 | 网关 → 设备（UDP 8585 广播） | `/user/all/custom/device/network_access/broadcast` |
| 2 设备发起认证 | 设备 → 网关（UDP 点对点） | `/user/all/custom/device/network_access/request` |
| 3 认证结果 + localSecret | 网关 → 设备（UDP 点对点） | `/user/all/custom/device/network_access/request_reply` |
| 4 上报设备 oid | 设备 → 网关（TCP 8586） | `/user/ipc/custom/device/list/upload` |
| 5 上报功能 sid | 设备 → 网关（TCP 8586） | `/base/{oid}/thing/topo/list/upload` |
| 6 控制下发 | 网关 → 设备 | `/base/{oid}/thing/property/down` → 回 `down_reply` |
| 7 状态上报 | 设备 → 网关 | `/base/{oid}/thing/property/up` |

关键点：**第 6 步是点对点的**——所以只要你的进程就是这台「三方设备」，
控制就会直接落到你的 TCP 连接上，不需要装驱动，也不需要截获别人的报文。

## 前置条件（唯一硬门槛）

入网是**代理认证**，验证信息要提前在 HDL 平台登记：

1. 在三方产品/设备登记这台设备的 `mac`、`spk`、厂商信息；
2. 平台给出 `macKey` / `secret`（或通过开放平台接口按 mac 取）；
3. 网关侧进入入网状态（Studio「入网」/ 网关按钮）。

没有登记的 mac 会在第 3 步被拒（`code != 200`），这时进程会直接报
「入网认证无响应 / 入网被拒」，不会假装成功。

## 运行

```bash
# 只验入网，不占 TCP（排障第一步）
python tools/vad_bridge.py tools/vad_config.example.json --auth-only

# 不调 HA，只打印 Studio 下发的控制（先验证链路）
python tools/vad_bridge.py tools/vad_config.example.json --dry-run

# 正式跑：把 down 翻成 HA 服务调用，把 HA 状态 property/up 推回网关
python tools/vad_bridge.py tools/vad_config.example.json
```

配置文件字段见 `tools/vad_config.example.json`：

- `gateway.host` 网关 IP；`gateway.local_secret` 留空则用入网拿到的密钥
- `device.oid` 8 字节 hex（厂商码 + 通讯方式 + 时间戳 + 产品类别）
- `device.mac` / `device.secret` 平台登记的值；也可直接给算好的 `device.mac_key`
- `entities[].entity_id` 对应 HA 实体；`spk` 决定卡片类型与属性
- `entities[].sid` 不填则按 `oid + 物模型 + 通道 + 0101` 自动生成（28 hex）

## 已在仓库里落的代码

- `custom_components/hdl_link/vad.py`：协议与状态机（入网、上报、down/read/up、ECB/CBC 双口径解密）
- `tools/vad_bridge.py`：CLI 桥（HA REST 读状态 / 调服务，`driver_logic.service_calls` 复用控制翻译）
- `tests/test_vad_bridge.py`：本地仿真网关端到端（入网 → 上报 → down → down_reply → read → up），13 项断言全过

## 验证边界（别含糊）

- **协议层已按官方文档实现并跑通仿真网关**（`python tests/test_vad_bridge.py`，0 项失败）。
- **真机未验**：2026-11-26 本机网络在 `192.168.10.x`，网关 `192.168.1.151` 已不在同一网段
  （UDP 8585 搜索无应答），因此这一步只能等接回现场网段后跑 `--auth-only`。
- 真机上最可能卡住的两点：平台登记（mac/spk/secret 是否已录）、网关是否处于入网态。

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `入网认证无响应` | 网关没开入网 / 不在同网段 / 8585 被占 | 先 `--auth-only`，确认网关 IP 与入网状态 |
| `入网被拒 code=xxx` | mac 未在平台登记，或 mac_key 算法不符 | 核对登记信息；必要时直接填平台给的 `mac_key` |
| 上报后 Studio 看不到卡片 | Studio 需要重新「读取」工程 | Studio 断开重连/重新读取 |
| 点控制无反应、抓包 0 条 | 设备 TCP 掉了（down 只发给设备连接） | 看日志「LINK TCP 断开」，保证进程常驻并重连 |
| `down` 解不出 JSON | 网关按 ECB 或 CBC 加密，密钥不符 | 日志会提示解密失败；核对 `localSecret` |
