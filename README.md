# HDL LINK for Home Assistant（HDL 官方版）

HDL（河东科技）官方推出的 Home Assistant 集成：把 HDL 主网关下的照明、开关、传感器、
窗帘与场景接入 Home Assistant，与 HA 自动化、面板、语音助手一起使用。

- **零配置门槛**：向导内自动搜索局域网内的 HDL 网关，选中即接入，无需填写总线地址
- **能力来自网关自身**：列出的是网关工程里真实存在的功能与场景，按名称/类型勾选即可
- **纯本地运行**：不依赖云端、不依赖 MQTT、不修改网关与设备固件
- **官方维护**：由 HDL 广州技术团队维护，随 HA 版本更新

支持安装方式：

- Home Assistant「Marketplace」（或 HACS「自定义存储库」）从本仓库安装
- 手动把 `custom_components/hdl_link` 拷入 HA 的 `config/custom_components/`

---

## 1. 支持的设备类型

| 设备类型 | HA 平台 | 说明 |
|---|---|---|
| 开关灯 | `light` | 开 / 关 |
| 调光灯 | `light` | 开 / 关 + 亮度（0–100%） |
| 色温灯 / RGB / RGBW 灯 | `light` | 当前按可调光接入（色温与颜色控制见「已知边界」） |
| 插座 / 通用继电器触点 | `switch` | 开 / 关 |
| 温度 | `sensor` | ℃ |
| 湿度 | `sensor` | %RH（网关配置了湿度传感器时出现） |
| 照度 | `sensor` | lx |
| CO₂ / PM2.5 / TVOC | `sensor` | ppm / µg/m³ |
| 人体感应 | `binary_sensor` | 有人 / 无人 |
| 门磁 / 烟雾 / 燃气 / 水浸 / 紧急求助 / 干接点 | `binary_sensor` | 报警 / 正常 |
| 百叶帘 / 卷帘 / 开合帘 / 开关停窗帘 | `cover` | 开 / 关 / 停 / 位置百分比 |
| 网关场景 | `button` | 每场景一个按钮，按下即执行 |

> 只接入网关工程里实际存在的功能；某类设备在工程里没有，列表里就不会出现。

---

## 2. 安装

### 方式 A：Marketplace / HACS（推荐，可在线更新）

1. 打开 **设置 → 加载项与市场（Marketplace）**，搜索并添加 `https://github.com/luxiangPS/hdl_link`（HACS 则在「集成 → 自定义存储库」中添加同一地址）。
2. 在市场中找到 **HDL LINK** → 安装 → 重启 Home Assistant。
3. **设置 → 设备与服务 → 添加集成 → 搜索 HDL LINK**。

### 方式 B：手动拷贝

```bash
cp -r custom_components/hdl_link /config/custom_components/
ha core restart
```

---

## 3. 配置向导

1. **选择 HDL 网关**
   向导会自动在局域网内搜索 HDL 网关，下拉列表显示形如 `MGWIP.430 · 192.168.1.150` 的条目，
   选中后点「提交」。若网络环境屏蔽了广播或跨网段，选「手动输入网关 IP」并填写网关地址。
2. **选择要接入的功能**
   列表来自网关工程，每项形如 `[照明] 走廊筒灯（开关灯）`。默认全选可接入项；
   页面上会显示「共 N 个功能，M 个可接入，K 个类型暂不支持」。
3. **本地加密密钥（可选）**
   网关若启用了本地加密上报，填写部署方提供的 16 位本地密钥即可获得实时状态推送；
   **留空也完全可用**，此时改为按兜底间隔读取状态（见下一节）。
4. **选择场景（可跳过）**
   勾选后每个场景生成一个按钮实体，点一下即在网关内执行。

完成后会生成：1 个网关设备 + 若干物理模块设备 + 各功能实体（开关、传感器、场景按钮等）。

---

## 4. 日常使用与设置

- **设备页**：设置 → 设备与服务 → HDL LINK → 设备，可见网关与下属物理模块（型号、固件版本取自网关）。
- **实体页**：按「照明 / 开关 / 数值传感器 / 二元传感器 / 窗帘 / 场景」分组。
- **状态实时更新**：网关在设备状态变化时主动上报，界面随即刷新，**默认不做周期轮询**（实例启动时读取一次全量状态作为初值）。
- **兜底轮询（可选）**：集成卡片 → 配置（选项）→ 兜底轮询间隔（秒）；填 `0` 关闭（默认）。也可在同一页更新本地加密密钥。

---

## 5. 现场自检脚本（可选，不需要 Home Assistant）

在任意装有 Python 3.9+ 的电脑上（与网关网络可达）：

```bash
python tools/live_check.py 192.168.1.150
```

脚本按顺序检查：连接网关 → 读取功能清单 → 读取场景 → 批量读取状态 → 选一路灯下发并回读、再恢复原状态。
输出会逐项打印 `OK` / `FAIL`，便于现场判断是网络、权限还是工程配置问题。

---

## 6. 系统要求

- HDL 主网关（LINK 网关，如 MGWIP 系列），固件支持本地接入
- 网关与 Home Assistant 网络可达（同网段最稳；跨网段需保证路由与端口放通）
- Home Assistant 2024.6.0 或更高

---

## 7. 已知边界

- 色温与颜色控制尚未开放（不同固件下键名不一致，避免做出宣称但不可用的能力）。
- 窗帘位置百分比按项目实测键名自适应，个别型号需现场核对。
- 湿度实体仅在网关工程里存在湿度传感器时出现。
- 房间信息可读取，但暂未用于自动分区。

---

## 8. 排障

| 现象 | 处理 |
|---|---|
| 向导第 1 步列表为空 / 「发现 0 台」 | 网关与 HA 不同网段或广播被屏蔽：改用「手动输入网关 IP」；确认 HA 与网关路由互通 |
| 提交时报「无法连接该网关」 | 检查 IP 是否正确、网关是否在线、防火墙是否放通集成的通信端口 |
| 功能列表为空 | 网关工程里没有配置设备功能，先用调试软件确认工程已下发 |
| 实体显示「不可用」 | 网关离线或网络中断；恢复后集成会自动重连并刷新 |
| 操作后状态更新慢 | 先确认已填写本地密钥（实时上报需要它）；仍不刷新时在选项里设置兜底轮询间隔（如 60 秒）

---

## 9. 目录

```
custom_components/hdl_link/
  __init__.py      集成入口（连接、平台加载、保活）
  protocol.py      通信层（帧编解码、长连接、重连）
  gateway.py       网关操作集合（发现、读工程、读写属性、执行场景）
  discovery.py     网关本地搜索（局域网广播）
  coordinator.py   状态协调器（上报驱动，兜底轮询可选）
  config_flow.py   配置向导（选网关 → 选功能 → 密钥 → 选场景）
  entity.py        实体基类
  light / switch / sensor / binary_sensor / cover / button
  const.py         类型映射与属性键
  brand/           品牌图标
  translations/    zh-Hans / en
tools/live_check.py 现场自检脚本
```

---

## 10. 支持

- 问题与需求：本仓库 Issues
- 版本记录见 Releases / 提交历史

---

## 11. HDL → HA：用场景事件做反向控制

网关**不会**把控制指令下发给 LINK 客户端（实测 `property/down` 只路由给驱动，客户端收不到），
所以反向控制只能用网关已有的上行通道：**HDL 场景执行事件**。

实测：执行 LINK 场景后，网关会推

```
/user/{gw}/custom/scene/status/up      # 场景执行
/user/{gw}/custom/logic/status/up      # 逻辑动作
/base/{gw}/thing/property/up           # 受影响设备的状态变化
```

这些帧和属性上报一样是 AES-128-CBC（key = iv = 本地密钥），所以**必须配置本地密钥**才能解析。
解析成功后集成会触发事件 `hdl_link_hdl_event`：

```yaml
# 自动化示例：HDL 面板按下「回家」场景 → 打开 HA 里的米家灯
alias: HDL 场景触发 HA 动作
trigger:
  - platform: event
    event_type: hdl_link_hdl_event
    event_data:
      kind: scene
      name: 回家走廊灯
action:
  - service: light.turn_on
    target:
      entity_id: light.keting
```

在 HDL 侧把面板按键绑定到该场景（面板绑定命令），面板按键就变成「HA 遥控器」，
延迟通常 1 秒内。

要真正的实时下行控制（网关把控制直接送到 HA 端），需要网关侧有一个注册驱动接收
`property/down`（`driver_name` + 驱动配置表），当前开源集成做不到这一步。

---

## 12. HA 驱动后端（供网关侧驱动对接）

集成内置了一套「驱动后端」接口，网关侧只要有驱动进程（driver_name=ha）就能直接对接：

| 接口 | 用途 |
|---|---|
| `GET /api/hdl_link/driver/devices` | 全量 HA 设备清单：实体、域、spk、当前状态、oid/sid、是否已注册 |
| `POST /api/hdl_link/driver/down` | 驱动把网关下行转给 HA 执行：`{"objects":[{"sid","status":[{key,value}]}]}` |
| `POST /api/hdl_link/driver/edit` | 编辑已注册功能：`{"entity_id","action":"rename|set_spk|delete","name"/"spk"}` |
| `POST /api/hdl_link/driver/register` | 把 sid 以 `driver_name` 上报网关（验证驱动是否就绪） |

现成的驱动代理脚本：`tools/hdl_ha_driver.py`（仅标准库，跑在局域网任意机器）

```bat
python tools\hdl_ha_driver.py --gateway 192.168.1.151 --name ha ^
       --ha-url http://172.26.30.104:8123 --ha-token <HA长效令牌>
```

**已知硬门槛**：`driverconfig/edit` 会校验驱动是否已安装，未安装直接返回
`500001「驱动不存在」`（实测）。所以脚本能跑通状态上报，但**收不到下行**——
除非网关侧已经装好名为 `ha` 的驱动（需要驱动安装包 / 驱动证书）。

---

## 13. 场景 SID → HA 动作（不用写自动化也能"面板控 HA"）

面板键绑定场景后，网关推 `scene/status/up`；集成按 **SID** 查本地映射表，命中就直接调 HA 服务：

```bash
# 查看收到的场景/逻辑 SID（seen 里会自动累积）
curl -H "Authorization: Bearer <token>" http://<ha>:8123/api/hdl_link/event_map

# 绑定：场景 sid → 开灯
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"sid":"0201036AFD43B20A0A0100011100","entity_id":"light.keting","action":"turn_on"}' \
  http://<ha>:8123/api/hdl_link/event_map
```

`action` 可选：`turn_on / turn_off / toggle / open / close / run`。
删除映射：同接口传 `entity_id: null`。
