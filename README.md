# HDL Buspro for Home Assistant（HDL 官方版）

HDL（河东科技）自研的 Home Assistant 集成，直连 HDL 网关的 **Buspro over IP（UDP 6000）**，
不依赖 MQTT、不依赖任何第三方库、不需要设备改固件。

支持通过 **HACS / Marketplace 从 GitHub 仓库安装**，也支持直接拷进 `config/custom_components/`。

---

## 1. 这套集成做了什么

| 层 | 文件 | 作用 |
|---|---|---|
| 协议层 | `protocol.py` | 报文组包/解包、CRC16-XMODEM、命令码与设备类型表 |
| 传输层 | `gateway.py` | asyncio UDP 客户端：请求-应答配对、状态缓存、事件回调 |
| 发现层 | `discovery.py` | 一次发完 250×2 条探测包，2.5 秒窗口内收全部应答 |
| 运行时 | `__init__.py` | 一网关一连接、通道 10s 轮询、传感器 20s 轮询、事件分发 |
| 配置 | `config_flow.py` | 填 IP → 自动扫描 → 勾选设备 → 指定调光/窗帘/门铃 |
| 实体 | `switch/light/cover/sensor/binary_sensor.py` | 五类实体，全部走 `HdlEntity` 基类 |

### 能力矩阵（v1.0，均已真机或协议级验证）

| HDL 能力 | 命令 | HA 实体 | 状态 |
|---|---|---|---|
| 继电器/调光通道开关 | `0x0031` 写 / `0x0033`→`0x0034` 读 | `switch` | ✅ 真机 8/8 通过 |
| 通道调光亮度 0–100 | `0x0031` | `light`（0–255 ↔ 0–100） | ✅ |
| 温湿度照度 | `0x1604`→`0x1605`，`0x1630` 上报 | `sensor`（温度/照度） | ✅ 真机 27.0°C / 69–300lx |
| 干接点 / 门铃 | `0x15D0` 广播（`status=0` 触发） | `binary_sensor`（6s 自动复位） | ✅ 真机广播已抓到 |
| 窗帘（脉冲式） | `0xE3E2` 开/关/停 | `cover`（无位置回读，`assumed_state`） | ✅ 协议级 |
| 空调 | `0x1938` | ❌ 本型号（3557/1513）不实现 | 留给 v1.1 |

> 关于“HA 状态变了 ≠ 设备动了”：本集成的控制效果以**总线广播（`0x0034`）为准**，
> 下发后靠网关主动广播覆盖本地乐观值，这一点和真机行为一致。

---

## 2. 安装

### 方式 A：HACS / Marketplace「从链接添加」（推荐，面向客户）

1. 把本仓库推到 HDL 官方 GitHub（例如 `https://github.com/HDL/hdl_buspro`）。
2. HA → **Marketplace（HACS）→ 右上 ⋮ → 自定义仓库 → 添加**，填仓库地址，类别选 `Integration`。
3. 搜索 `HDL Buspro` → 下载 → **重启 HA**。
4. 设置 → 设备与服务 → **添加集成** → 搜 `HDL Buspro`。

> Marketplace 只负责把文件放进 `config/custom_components/hdl_buspro`，
> 装完必须再做一次「添加集成」，否则不会出现实体。

### 方式 B：手动拷贝

把 `custom_components/hdl_buspro/` 整个目录复制到 HA 的 `/config/custom_components/`，重启 HA。

### 安装后必做：释放 UDP 6000

HDL 网关**只把应答回给绑定 6000 的那个客户端**。以下程序必须关掉，否则集成扫描不到设备：

- `HDL Buspro Setup Tool` / `Buspro Studio`（会绑定 `本机IP:6000`，优先级比 `0.0.0.0:6000` 更高）
- 旧的 MQTT 桥接脚本（`buspro_spec_bridge.py`）

排查命令（Windows）：`Get-NetUDPEndpoint -LocalPort 6000`，正常应只有一条 `0.0.0.0 6000`。

---

## 3. 配置流程（三步）

1. **连接 HDL 网关**：网关 IP（如 `192.168.1.150`）、UDP 端口 `6000`、扫描子网号（现场是 `10`）。
   - 集成会自动广播扫描，把命中的设备列出来。
   - 报错对照：`port_in_use` = 本机 6000 被占；`no_devices` = 子网号不对或总线没接；`cannot_connect` = IP/网络问题。
2. **选择要接入的设备**：多选列表，显示形如 `10.100 · 3557 · 48 通道`、`10.3 · 1513 · 温湿照 · 27°C / 300lx`。
   - 有通道的判为继电器/调光设备；只应答 `0x1605` 的判为传感器。
3. **分配通道用途**（可留空）：格式 `设备地址/通道号`，逗号分隔。
   - 调光通道 → `light`；窗帘通道 → `cover`；门铃设备地址 → `binary_sensor`。
   - 例如：调光 `10.100/9, 10.100/10`，窗帘 `10.100/23, 10.100/24`，门铃 `10.3`。
   - 未填的通道一律作为 `switch`。

配置后可在 **设置 → 设备与服务 → HDL Buspro → 选项** 里修改通道用途，保存即自动重载。

---

## 4. 验证

### 4.1 无真机：协议级端到端（已跑通 11/11）

```powershell
cd hdl_buspro-integration
python -m tests.test_end_to_end
```

会启动模拟网关（`127.0.0.1:6100`），验证：扫描发现 2 台设备 → 读回 48 路 → 控制生效且广播回读 →
温度 27.0°C / 照度 300lx → 收到门铃广播。

也可以单独跑模拟网关，用 HA 去连它（改一下 `sim_gateway.py` 的地址即可）：

```powershell
python tests/sim_gateway.py
```

### 4.2 真机：直接验证集成代码（不经过 HA/MQTT）

前置：关闭 Buspro Studio / Setup Tool，并停掉旧桥接（`get -NetUDPEndpoint -LocalPort 6000` 只剩一条）。

```powershell
python tools\probe_real.py --host 192.168.1.150 --subnet 10 --channel 9 --source-ip 192.168.1.152
```

判定四条：扫描有设备 → 读回通道 → 关/开各有一致的总线广播 → 传感器数值合理。

> 脚本会自己检查 6000 是否空闲；被占用会直接退出码 2 并提示要关哪个程序。

### 4.3 在 HA 里看结果

- 实体命名：`HDL 10.100` 下的 `通道1…通道48`；`HDL 10.3` 下的 `温度`、`照度`、`门铃`。
- 开发者工具 → 状态，能看到 `switch.hdl_10_100_*`、`sensor.hdl_10_3_temperature`。
- 日志：`设置 → 系统 → 日志`，筛选 `custom_components.hdl_buspro`。

---

## 5. 代码地图（给后续维护/扩展的人）

```
custom_components/hdl_buspro/
├── manifest.json         domain=hdl_buspro, iot_class=local_push, requirements=[]
├── const.py              配置键 / 轮询周期 / 设备种类
├── config_data.py        data 与 options 合并
├── protocol.py           报文格式、CRC、命令码、payload 编解码
├── gateway.py            HdlGateway：async_send / async_request / 状态缓存 / 监听器
├── discovery.py          async_scan：批量探测 + 窗口收集
├── __init__.py           HdlRuntime：轮询任务、事件分发、平台加载
├── entity.py             HdlEntity：device_info、unique_id、dispatcher 订阅
├── config_flow.py        三步配置 + 选项流程
├── switch.py / light.py / cover.py / sensor.py / binary_sensor.py
└── strings.json + translations/{en,zh-Hans}.json
```

### 加一类新能力（例如空调、风机、场景）

1. `protocol.py` 加命令码常量与 payload 构造函数（参考 `build_curtain_command`）。
2. `gateway.py` 若要读状态，在 `RESPONSE_CODES` 里加「请求码 → 应答码」。
3. 新建 `climate.py`，继承 `HdlEntity`，在 `__init__.py` 的 `PLATFORMS` 增加 `Platform.CLIMATE`。
4. 在 `config_flow.py` 的第 3 步增加一个文本框（例如“空调设备地址”），写进 `entry.data`。
5. `strings.json` + 两个翻译文件补文案。

> 设备类型号可从 `DEV_TYPE_NAMES` 扩展；未知型号不会报错，只是显示为 `HDL 总线设备 <type>`。

---

## 6. 已知边界（如实说明）

- **单客户端**：一个网关同一时间只能被一个进程占用 6000。集成、桥接、调试工具三者互斥。
- **窗帘无位置**：`0xE3E2` 是脉冲命令，没有位置回读，`cover` 标记为 `assumed_state`。
- **空调未实现**：本现场设备（3557/1513）不带空调对象，`0x1938` 留给 v1.1。
- **通道语义靠人工映射**：48 路通道物理含义（灯/插座/风机）无法从总线读出，需要在配置第 3 步人工指定或后续做“逐路标定”。
- **v1.0 无诊断导出**：`diagnostics.py` 未写（建议 v1.1 加，便于客户报障）。
- **未提交 HA 官方 core**：当前是 `custom_components`，走 HACS/Marketplace；进 core 需要满足 HA 的质量检查与品牌资产要求。

---

## 7. 与第三方 `ar_hdl_buspro` 的差异

| 维度 | 第三方 ar_hdl_buspro v5.0.18 | 本集成 v1.0 |
|---|---|---|
| 授权 | 需要厂商 license，实测 `pending_approval` | 无授权校验 |
| 体量 | ~450KB，含 151KB 目录表、135KB config_flow、41KB licensing | 约 30KB，纯标准库 |
| 依赖 | 自带 `pybuspro` | 无第三方依赖 |
| 覆盖 | 空调/面板/按键 LED/虚拟设备等全量能力 | 开关/调光/窗帘/温湿照/门铃 |
| 定位 | 商用成品 | 官方底座，按项目需要扩 |

---
