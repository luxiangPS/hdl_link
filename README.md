# HDL LINK for Home Assistant（HDL 官方版）

把 HDL 主网关下的照明、开关、传感器、窗帘与场景接入 Home Assistant。

**纯本地协议对接，不在网关侧安装任何驱动、不依赖云端。** 集成作为「本地三方应用」直接连网关 TCP 8586，
按 HDL 开放平台《本地协议对接》文档收发 JSON 主题帧：读功能点 / 属性 / 场景 / 房间，下发控制，周期读回状态。
网关重启、HA 重启都不需要往网关里塞脚本。

---

## 1. 原理

```
HA（本集成）  ──TCP 8586──▶  HDL 网关  ──Buspro / KNX / LINK──▶  模块与面板
     ▲                                                              │
     └────────── 周期 property/read（+ 可选主动上报） ◀──────────────┘
```

- 发现：UDP 8585（组播 `239.0.168.188` + 广播）发 `/user/all/custom/gateway/search`，网关回 `search_reply`，
  其中 `oid` 就是本集成所有主题里的 `${gw}`
  > `oid`（如 `010105003DF6F100`）与 `gatewayId`（云 ID，如 `2109…`）不是一个东西，本地主题用的是 **oid**

- 读工程：`/user/{oid}/custom/function/list/get`、`function/attribute/get`、`scene/list/get`、`room/list/get`
- 控制：`/base/{oid}/thing/property/down`
- 状态：`/base/{oid}/thing/property/read` 周期读（默认 5 秒，可配）

实测（MGWIP.430 / V03.11.19）：66 个功能点、场景列表、房间树全部可读，单次读往返 5~19 ms，控制回执 `code=0 / tag=Success`。

---

## 2. 安装

1. HACS → 自定义存储库 `luxiangPS/hdl_link` 下载，或把 `custom_components/hdl_link` 整目录拷到 HA 的 `config/custom_components/`
2. 重启 Home Assistant
3. 设置 → 设备与服务 → 添加集成 → 搜索 **HDL LINK**

无第三方 Python 依赖；`cryptography`（HA 自带）仅在填写「本地加密密钥」时用于解上报帧。

---

## 3. 配置流程

| 步骤 | 内容 |
|---|---|
| 1 | 自动搜索网关（UDP 8585），也可手填 IP |
| 2 | 连接并读网关信息（型号 / oid / MAC） |
| 3 | 勾选要接入的功能点（按 spk 自动判定平台） |
| 4 | 可选：本地加密密钥（16 位）。填了才能解网关的加密主动上报；不填就纯轮询 |
| 5 | 选场景 → 生成按钮实体 |

选项里可改「轮询间隔」：`0` = 关闭轮询（只靠主动上报），默认 `5` 秒。

---

## 4. 支持的设备类型

| 设备类型 | HA 平台 | 说明 |
|---|---|---|
| 开关灯 / 调光 / 色温 / RGB(W) | `light` | 开 / 关 / 亮度（色温与颜色见「已知边界」） |
| 插座 / 通用继电器触点 | `switch` | 开 / 关 |
| 温度 / 湿度 / 照度 / CO₂ / PM2.5 / TVOC | `sensor` | ℃ / % / lx / ppm / µg/m³ |
| 人体 / 门磁 / 烟感 / 燃气 / 水浸 / 求助 / 干接点 | `binary_sensor` | 报警 / 正常 |
| 百叶 / 卷帘 / 开合帘 / 开关停窗帘 | `cover` | 开 / 关 / 停 / 位置百分比 |
| 空调 / 中央空调 | `climate` | 开关 / 模式 / 风速 / 设定温度 / 室温 |
| 风机 | `fan` | 开关 + 档位 |
| 新风 / 加湿 | `humidifier` | 开关 + 模式，回传 CO₂ / PM2.5 / 湿度 |
| 门锁 / 阀门 / 扫地机 | `lock` / `valve` / `vacuum` | 开锁、开关阀、启动回充 |
| 网关场景 | `button` | 每场景一个按钮，按下即执行 |

同一设备下重名会自动补通道号（如「客厅灯（通道 02）」）。

---

## 5. 服务与事件

| 服务 | 作用 |
|---|---|
| `hdl_link.bind` | 把 HDL 面板按键 / 场景 / 逻辑的 SID 绑定到 HA 实体：`mode=action` 每按一次执行一次动作（面板键当遥控器），`mode=follow` 跟随属性状态 |
| `hdl_link.scene_list` | 列出网关场景 + 绑定状态 + 失效绑定，排查「面板按了没反应」 |

每条 HDL 上行都会在事件总线 fire `hdl_link_hdl_event`，字段 `gateway / kind / sid / name / raw`，可直接在自动化里用。

---

## 6. 协议速查

| 用途 | Topic | 说明 |
|---|---|---|
| 搜索网关 | `/user/all/custom/gateway/search`（UDP 8585） | 组播 + 广播 |
| 功能列表 | `/user/{oid}/custom/function/list/get` | JSON |
| 属性定义 | `/user/{oid}/custom/function/attribute/get` | JSON |
| 场景列表 / 执行 | `/user/{oid}/custom/scene/list/get` / `scene/execute` | JSON |
| 房间 | `/user/{oid}/custom/room/list/get` | JSON |
| 读状态 | `/base/{oid}/thing/property/read` | JSON |
| 控制 | `/base/{oid}/thing/property/down` | JSON |
| 状态上报 | `/base/{oid}/thing/property/up` | 未配密钥时是加密二进制 |

TCP 帧格式：

```
Topic:/base/{oid}/thing/property/down\r\n
Length: <body 字节数>\r\n
\r\n
{"id":"…","objects":[…],"time_stamp":"…"}
```

---

## 7. 与旧版（≤2.9.x）的区别

2.x 会在网关里自动安装一个 Python「HA 驱动」，用来把 HA 设备反写进 HDL（网关 App/面板可控 HA、HA 状态推到 HDL）。
这需要往网关塞文件、走 OTA、占用网关资源，还要长期维护驱动版本。

3.0.0 起彻底去掉网关侧驱动，只保留 **HDL → HA** 方向（读、控、事件）。因此：

- 不再有「安装集成时检测并安装驱动」
- 不再有「设备同步（HA → HDL 写入）」面板，`scan / provision / sync` 三个服务一并移除
- 状态默认靠轮询（5 秒），间隔越小越跟手，代价是网关请求变多

---

## 8. 已知边界

- **状态实时性**：不配本地密钥时，网关不保证把属性变化推给第三方应用会话，靠轮询兜底；调小间隔可改善
- **色温 / 颜色**：色温灯目前按可调光接入（亮度可用，色温下发未验证）
- **进度类数据**：网关的 OTA / 驱动进度上行帧是加密且非 JSON，本集成不解析

---

## 9. 常见问题

**连不上**：确认 8586 可达（`Test-NetConnection <网关IP> -Port 8586`）。实测网关只开放 22 / 8586，没有本地 REST。

**控制没反应**：先看日志里 `property/down` 的回执，`code=0` 表示网关已接收，实际动作取决于模块与工程配置；
sid 是否在功能列表里可用 `hdl_link.scene_list` 或功能列表核查。

**网关重启后**：集成自动重连（`LinkClient` 内置重连），无需手动重载配置。

---

## 许可证

MIT
