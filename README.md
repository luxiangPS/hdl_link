# HDL LINK for Home Assistant（HDL 官方版）

HDL（河东科技）自研的 Home Assistant 集成，通过 HDL 官方 **Link 协议**直连 HDL 主网关
（MGWIP / LINK 网关，如 MGWIP.430），**不依赖 MQTT、不依赖第三方库、不改设备固件、不占用 UDP 6000**。

- 支持从 GitHub 仓库直接添加（HACS「自定义存储库」/ Home Assistant「Marketplace」）
- 支持直接把 `custom_components/hdl_link` 拷进 HA 的 `config/custom_components/`
- 网关能力来源是**读取网关内部工程数据**（功能清单 + 场景清单），不是靠扫描设备猜类型

---

## 1. 工作原理

| 层 | 文件 | 作用 |
|---|---|---|
| 协议层 | `protocol.py` | Link 报文封装与解析（`Topic` + `Length` + JSON 载荷），TCP 长连接、报文编号匹配、断线重连 |
| 网关层 | `gateway.py` | 搜索网关、读物理模块/功能/场景/房间、批量读属性、下发属性、执行场景 |
| 发现层 | `discovery.py` | 扫描本机各 IPv4 网段的 254 个地址并发探测，自动列出可接入网关 |
| 数据层 | `coordinator.py` | 单连接批量轮询（默认 10 秒，可调），控制后立即回读 |
| 平台层 | `light/switch/sensor/binary_sensor/cover/button.py` | 按 SPK 物模型映射为 HA 实体 |

要点：

1. **连接**：一个配置条目 = 一台主网关，一条 TCP 长连接（默认 8586），心跳保活。
2. **发现**：配置向导进来就自动搜网，用户只需在列表里选网关；搜不到时才手动填 IP。
3. **能力**：配置向导连接网关后调用网关内的功能清单接口，把每个功能的 `名称 / 类型(SPK)` 列给用户勾选。
4. **状态**：批量属性读取（一次最多 40 个功能），轮询周期可调。
5. **控制**：属性下发报文，网关确认后集成立刻回读一次，UI 状态跟手。

---

## 2. 支持的类型（当前版本）

| SPK | HA 平台 | 实体说明 |
|---|---|---|
| `light.switch` | light | 开关灯（开 / 关） |
| `light.dimming` | light | 调光灯（开关 + 亮度，网关 `current` 0~1 映射 0~255） |
| `light.cct` / `light.rgb` / `light.rgbw` | light | 目前按可调光接入（色温/颜色键未在文档固定，后续版本补） |
| `panel.socket` / `other.common` | switch | 插座 / 通用继电器触点 |
| `sensor.temperature` | sensor | 温度（℃，属性 `temperature`） |
| `sensor.humidity` | sensor | 湿度（%） |
| `sensor.light` | sensor | 照度（lx，属性 `illuminance`） |
| `sensor.co2` / `sensor.pm25` / `sensor.tvoc` | sensor | CO₂ / PM2.5 / TVOC |
| `sensor.pir` | binary_sensor | 人体感应（`people_status`） |
| `sensor.doorwindow` / `smoke` / `gas` / `water` / `help` / `dryContact` | binary_sensor | 门磁 / 烟雾 / 燃气 / 水浸 / 求助 / 干接点 |
| `curtain.shades` / `roller` / `trietex` / `switch` | cover | 窗帘（位置百分比自适应 `position`/`percent`，未验证的类型需现场确认） |
| 网关内场景 | button | 每个场景一个按钮，按一下即执行 |

> 类型映射在 `const.py` 的 `SPK_PLATFORM` / `SENSOR_KEYS` / `BINARY_SENSOR_KEYS` 里集中维护，新增类型只改这张表 + 对应平台文件。

---

## 3. 安装

### 方式 A：按仓库添加（推荐，可更新）

1. HACS → 集成 → 右上角「自定义存储库」→ 添加 `https://github.com/luxiangPS/hdl_link`，类型选「集成」。
2. 在列表里找到 **HDL LINK** → 下载 → 重启 Home Assistant。
3. 设置 → 设备与服务 → 添加集成 → 搜 **HDL LINK** → 按第 4 节走配置向导。

### 方式 B：手动拷贝

```bash
# 把 custom_components/hdl_link 整个目录拷到 HA 配置目录下
cp -r custom_components/hdl_link /config/custom_components/
# 重启
ha core restart
```

---

## 4. 配置向导（4 步）

1. **选择 HDL 网关**
   向导会自动扫描当前网络，下拉列表里出现形如 `MGWIP.430 · 192.168.1.150 · MGWIP.430` 的条目；
   找不到就选「手动输入网关 IP」，下一步填 IP。
   *（不需要填写总线子网号 —— 子网是 Buspro 的概念，Link 协议用功能 sid 直接寻址。）*

2. **选择要接入的功能**
   列表来自网关内的功能清单，每项显示 `[类型] 功能名称（类型说明）`，例如
   `[照明] Ulaz hodnik ugradna（开关灯）`。
   默认全选可直接接入的类型；不支持的类型不会出现在列表里（页面上会显示「x 个类型暂不支持」）。

3. **选择场景**（可跳过）
   列出网关内的场景（如「回家走廊灯」），勾选后生成按钮实体。

4. **完成**
   生成 1 个网关设备 + N 个物理模块设备（按 oid 分组）+ 各功能实体。

> 想调整轮询周期：集成页面 → 配置（选项）→ 轮询周期（3~120 秒）。

---

## 5. 在前端验证

- 设置 → 设备与服务 → **HDL LINK** → 设备：应看到网关与各物理模块（型号、固件版本来自网关工程）。
- 实体页按「照明 / 开关 / 数值传感器 / 二元传感器 / 场景」分组显示。
- 开关/照明点一下，现场继电器应立即动作，实体状态在 1 秒内回读一致。

---

## 6. 现场自检（不需要 Home Assistant）

在任意装有 Python 的设备上（与网关同网段，或网络可达）：

```bash
python tools/live_check.py 192.168.1.150
```

脚本会依次验证：搜索网关 → 读功能清单 → 读场景 → 批量读状态 → 选一路灯下发并回读、再恢复原状态。

实测输出（MGWIP.430，192.168.1.150）：

```
== 1. LINK 广播搜索 ==   oid=010105003DF6F100  gatewayId=2109921361567219716
== 2. 功能清单 ==       53 个：light.switch×48、other.common×2、sensor.pir/temperature/light 各 1
== 3. 场景列表 ==       回家走廊灯
== 4. 批量读取 ==       已读到 53/53 个功能的状态
== 5. 控制验证 ==       on_off on -> off，网关确认 True，回读 off，恢复 on，最终回读 on
```

---

## 7. 最小系统要求

- HDL 主网关（LINK 网关）固件需支持 Link 本地协议（本版本实测 `LD_MGWIP.430_V03.11.19_260626_RK3308_LINK`）
- 网关与 HA 之间网络可达（TCP 8586；同网段最稳，跨网段需保证路由/端口放通）
- Home Assistant 2024.6.0 以上（当前实测 2026.11.2）

---

## 8. 已知边界

- 网关主动上报的属性推送在本地是**加密载荷**，本集成不解析，统一用批量轮询取状态（因此是 `local_polling`，不是 `local_push`）。
- 色温 / 颜色控制尚未开放（属性键未在 HDL 文档中固定，避免做出假能力）。
- 窗帘类型已实现但现场键名需按项目确认。
- 房间信息（`room/list/get`）已能读，暂未用于自动分区。

---

## 9. 目录

```
custom_components/hdl_link/
  __init__.py      集成入口（连接、平台加载、心跳）
  protocol.py      Link 报文编解码 + TCP 客户端
  gateway.py       网关操作集合
  discovery.py     网段扫描发现
  coordinator.py   轮询协调器
  config_flow.py   配置向导（选网关 → 选功能 → 选场景）
  entity.py        实体基类
  light/switch/sensor/binary_sensor/cover/button.py
  const.py         主题、类型映射、属性键
  brand/           品牌图标（icon/logo/dark_logo）
  translations/    zh-Hans / en
tools/live_check.py  现场自检脚本
```
