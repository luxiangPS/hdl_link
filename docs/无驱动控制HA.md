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

## 在集成里填 / 改 macKey 与 secret

入口：**设置 → 设备与服务 → HDL LINK → 配置**（配置条目右侧「配置」按钮），
选项菜单里有两项：

- 「轮询与本地密钥」：原有 `poll_interval` / `local_secret`
- 「无驱动反向控制（Studio / 面板可控 HA）」：填三方设备身份

表单字段：

| 字段 | 说明 |
|---|---|
| 启用反向控制 | 打开后保存即开始入网；失败每 60 秒重试 |
| 设备名称 | 显示在 HDL 设备表里的名字 |
| 设备 MAC | 平台登记的 mac，16 位 hex，容忍冒号/大写（自动归一） |
| Secret | 平台登记的 secret，密码框；**原样作为 `auth.mac_key` 发送** |
| macKey | 平台给的 macKey，优先于 Secret；填了就用它原值 |
| 设备 oid | 16 位 hex，默认 `04E8030100000A01` |
| 要暴露给 HDL 的 HA 实体 | 实体选择器（灯 / 开关 / input_boolean / 风扇 / 窗帘 / 传感器 / 二元传感器） |

macKey 口径（实测 MGWIP.430）：平台给的设备密钥就是 `auth.mac_key` **原值**，
不需要再做 md5。集成先按原值入网，若网关回 `124013 Failure` 会自动退回
`md5(md5(mac+secret))` 再试一次。设备 MAC 一律按**大写**上报（网关按平台登记值
逐位比对，小写会回 `124014`）。

### HA 跨网段 / 虚拟机 NAT 时：跑 UDP 中继

LINK 入网固定把 `request_reply` 发到「请求来源 IP 的 8585」。HA 若经 Hyper-V
Default Switch NAT 出去，源端口被改写（8585 → 随机端口），回包到不了 HA，日志
表现为「入网认证无响应」。在与网关同网段的机器上跑一次中继即可：

```bash
python tools/udp_relay_8585.py --gateway 192.168.1.151 --ha 172.26.30.104
```

HA 那边不用改任何配置，入网成功后中继可以关掉（之后只剩 TCP 8586）。

保存后：选项写入配置条目 → 条目自动重载 → 集成内 `VadExporter` 立刻开始
「入网 → 上报 oid/sid → 等 Studio 下发」。spk 由实体能力自动判定
（带亮度的灯 → `light.dimming`，窗帘 → `curtain.trietex`，开关 → `other.common`，
传感器按 device_class 对应）。

改 secret / macKey：再进同一个菜单改字段保存即可，不需要删条目重建。

## 运行（两种方式，任选）

### A. 集成内运行（推荐）

按上面填完选项，重载 HDL LINK 条目即可，日志关键字 `反向控制` /
`Studio 控制`。同时保留原有的 HDL → HA 方向。

### B. 独立进程运行（不依赖 HA 集成生命周期）

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
- `custom_components/hdl_link/vad_export.py`：集成内运行时（选项 → 入网 → 上报 → 下行转 HA 服务 → 状态上行）
- `custom_components/hdl_link/config_flow.py`：选项菜单 `basic` / `vad`，macKey / secret 可填可改
- `tools/vad_bridge.py`：CLI 桥（HA REST 读状态 / 调服务，`driver_logic.service_calls` 复用控制翻译）
- `tests/test_vad_bridge.py`：本地仿真网关端到端（入网 → 上报 → down → down_reply → read → up），17 项断言全过

## 验证边界（别含糊）

- **协议层已按官方文档实现并跑通仿真网关**（`python tests/test_vad_bridge.py`，0 项失败）。
- **真机已验（2026-11-26，网关 MGWIP.430 / fw V03.11.19，HA Core 2026.11.2）**：
  - 方向 HA → 网关（状态上报）：可用。HA 侧 `light.turn_on` 后，网关读回该虚拟功能点 `on_off=on`；
    网关设备表里虚拟设备 `online=true`。
  - 方向 网关 → HA（`property/down` 控制）：**网关不转发**。详见下一节。
  - 场景执行事件反向控制：可用（见「3.3.0 场景桥」）。

## 3.3.0 场景桥：网关不转发 down 时的可用通道（真机实测）

### 实测结论

| 试验 | 结果 |
|---|---|
| 授权连接下发 `/base/{DEV}/thing/property/down` | 网关回 `code=0, objects[{sid, tag:"Success"}]`，但设备 TCP 连接 **0 字节**（原始字节级读 12 秒） |
| 同连接同 topic 下发给真实 buspro 灯 | 立即生效 → 下行通道本身与报文格式没问题 |
| 完整官方流程（UDP/TCP 入网 → upload → topo → up → 心跳）后再下发 | 同样 0 帧 |
| 入网请求补 `ip_address`、设备表补 `addresses` / `driver_code` / `sid` / `sids` / `online` | 设备表字段仍为空，down 仍不转发 |
| 下发期间监听 UDP 8585（含组播 239.0.168.188）、TCP 8586/8587/8588/6000 | 没有任何设备方向报文或入站连接 |

结论：**这台网关的 LINK 应用对三方设备只做「收 up、回 down_reply」，不把控制帧路由给设备连接**，
与 HA 侧代码无关，也不是入网方式、字段缺失或 NAT 造成的。

### 可用的控制通道：场景执行广播

网关会广播：

```
/user/{gatewayId}/custom/scene/status/up   场景执行
/user/{gatewayId}/custom/logic/status/up   逻辑执行
/base/{gatewayId}/thing/property/up        真实设备状态变化
```

其中 `scene/status/up` 是**广播**，任意 TCP 客户端都能收到（实测另一条连接 8 秒内收到 1 帧）。
Studio / App / 面板点场景、按绑定到场景的按键，走的都是这条。

### 用法：一条服务，自动建场景 + 自动绑定

```yaml
action: hdl_link.scene_bridge
data:
  entity_id: light.b1_f1_0104_0201016afd412506020100080101
  name: B1-F1-0104 通道 08
  # entry_id: 多网关时指定
```

它做三件事：

1. 按实体 id 生成一对稳定 sid：`020103` + md5(实体) 前 6 字节 + `01` + `0001`（开）/`0002`（关）+ `0001`，共 14 字节；
2. 在网关里建两条空动作场景：`<name> 开` / `<name> 关`（`status` 必须是空数组 `[]`，否则网关不落库）；
3. 调 `hdl_link.bind` 把两条场景分别绑成 `turn_on` / `turn_off`。

之后在 Studio / HDL App / 面板（按键绑场景）里点这两条场景，就是控制该 HA 实体。
返回值为每条场景的 `sid / name / add_code / present`，`present=true` 表示回读网关场景列表时确实存在。

### sid 为什么这样拼（踩过的坑）

| 形态 | 结果 |
|---|---|
| 只在「设备段」不同、序号段相同 | 后建的把先建的顶掉，list 里只剩一条 |
| 设备段相同、序号段（`0001`/`0002`）不同 | 两条并存 ✅ |
| 总长不是 28 hex | `505007 Sid字符非法` |

### 真机验证（3.3.1，2026-11-26）

```
hdl_link.scene_bridge → 两条 present=true
网关执行『客厅灯 开』 → HA 日志 上行批次 kind=scene → homeassistant.turn_on → 实体 on
网关执行『客厅灯 关』 → homeassistant.turn_off → 实体 off
```

网关执行场景与 Studio / App / 面板按键触发是同一条 `/custom/scene/execute` 路径，所以这条链路即等同于在 HDL 侧点场景。

### 局限（说清楚）

- HDL 侧看到的是**场景**，不是一台设备卡片；面板按键要绑到场景而不是"设备"。
- 开/关分成两条场景（事件只带 sid + status，不带具体值）。
- **网关场景总量约 5 条**，一条实体开/关就占 2 条，新增会挤掉最早的 → 实际只适合 2 个实体；需要更多时得考虑给网关换固件或另找广播通道。
- 场景里自带的动作只能指向真实 buspro 设备，指向 HA 虚拟设备回 `505007`。
- 虚拟设备本身仍然保留：它负责把 HA 状态同步回 HDL（App 里能看到状态）。
- 拆除用 `hdl_link.scene_bridge_remove`（删场景 + 清绑定）。

### 为什么用「空动作场景」而不是场景里放设备

场景本身不需要任何 objects —— 它的价值只是「被执行时网关会广播一次事件」。
所以不要在场景里塞 HDL 设备动作，否则会额外动真实回路。

### 排障
| 现象 | 原因 | 处理 |
|---|---|---|
| `入网认证无响应` | 网关没开入网 / 不在同网段 / 8585 被占 | 先 `--auth-only`，确认网关 IP 与入网状态 |
| `入网被拒 code=xxx` | mac 未在平台登记，或 mac_key 算法不符 | 核对登记信息；必要时直接填平台给的 `mac_key` |
| 上报后 Studio 看不到卡片 | Studio 需要重新「读取」工程 | Studio 断开重连/重新读取 |
| 点控制无反应、抓包 0 条 | 设备 TCP 掉了（down 只发给设备连接） | 看日志「LINK TCP 断开」，保证进程常驻并重连 |
| `down` 解不出 JSON | 网关按 ECB 或 CBC 加密，密钥不符 | 日志会提示解密失败；核对 `localSecret` |
