/**
 * HDL 设备同步 —— 面板前端（2.3.0）
 *
 * 产品结构：
 *   1) 顶部：做什么、扫描结果概览
 *   2) 工具条：搜索 / 筛选 / 是否显示系统实体
 *   3) 设备卡片：设备名 + 区域 + 来源集成 + 每个功能「当前状态 → 将写成什么」
 *   4) 底部固定操作条：已选多少 + 预览计划 / 写入网关
 *   5) 结果表：逐项 oid / spk / 结果
 */

const DOMAIN_LABEL = {
  light: "灯",
  switch: "开关",
  cover: "窗帘",
  climate: "空调",
  fan: "风机",
  media_player: "影音",
  lock: "门锁",
  vacuum: "扫地机",
  water_heater: "热水器",
  humidifier: "加湿器",
  valve: "水阀",
  sensor: "传感器",
  binary_sensor: "状态传感器",
};

const SPK_LABELS = {
  "light.switch": "开关灯",
  "light.dimming": "调光灯",
  "light.cct": "色温灯",
  "light.rgb": "RGB 灯",
  "light.rgbw": "RGBW 灯",
  "panel.socket": "插座",
  "other.common": "通用开关",
  "curtain.switch": "开关停窗帘",
  "curtain.trietex": "开合帘",
  "curtain.roller": "卷帘",
  "sensor.temperature": "温度",
  "sensor.humidity": "湿度",
  "sensor.light": "照度",
  "sensor.co2": "CO₂",
  "sensor.pm25": "PM2.5",
  "sensor.pir": "人体感应",
  "sensor.doorwindow": "门磁",
};

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 把 HA 的状态翻译成人话 */
function stateText(fn) {
  const raw = String(fn.state || "");
  if (!raw || raw === "unavailable") return "不可用";
  if (raw === "unknown") return "未知";
  const domain = fn.domain;
  if (domain === "light" || domain === "switch" || domain === "fan") {
    if (raw === "on") return "开";
    if (raw === "off") return "关";
  }
  if (domain === "cover") {
    const map = { open: "已打开", opening: "打开中", closed: "已关闭", closing: "关闭中" };
    if (map[raw]) return map[raw];
  }
  if (domain === "lock") {
    return { locked: "已上锁", unlocked: "未上锁", locking: "上锁中", unlocking: "解锁中" }[raw] || raw;
  }
  if (domain === "binary_sensor") {
    return { on: "触发", off: "正常" }[raw] || raw;
  }
  const num = Number(raw);
  if (!Number.isNaN(num) && raw.trim() !== "" && fn.unit) return `${raw} ${fn.unit}`;
  return raw.length > 16 ? raw.slice(0, 16) + "…" : raw;
}

/** 状态颜色：开=主色，关=灰，异常=红 */
function stateTone(fn) {
  const raw = String(fn.state || "");
  if (raw === "unavailable" || raw === "unknown") return "muted";
  if (["on", "open", "locked", "opening"].includes(raw)) return "on";
  if (["off", "closed", "unlocked"].includes(raw)) return "off";
  return "value";
}

/** 面板本地偏好（收起状态 / 手动移除的不可用功能）存这里 */
const PREFS_KEY = "hdl_link_panel_prefs_v1";

class HdlLinkProvision extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._booted = false;
    this._scan = null;
    this._busy = false;
    this._result = null;
    this._query = "";
    this._filter = "all"; // all | control | sensor | unsupported
    this._showSystem = false;
    this._gatewayLabel = "HDL 网关";
    this._dryRun = false;
    this._checked = new Map(); // entity_id -> spk
    this._renderQueued = false;
    // 页签：provision = 设备同步 / bridge = 按键桥（面板 → HA）
    this._tab = "provision";
    this._bridge = null;
    this._bridgeBusy = false;
    this._bridgeError = "";
    this._bridgeQuery = "";
    // 面板本地偏好：收起的设备、被手动移除的不可用功能（只影响展示，不写入网关）
    this._collapsed = new Set();
    this._removed = new Set();
    this._restorePrefs();
  }

  _restorePrefs() {
    try {
      const raw = window.localStorage.getItem(PREFS_KEY);
      const prefs = raw ? JSON.parse(raw) : {};
      if (Array.isArray(prefs.collapsed)) this._collapsed = new Set(prefs.collapsed);
      if (Array.isArray(prefs.removed)) this._removed = new Set(prefs.removed);
    } catch (err) {
      this._collapsed = new Set();
      this._removed = new Set();
    }
  }

  _savePrefs() {
    try {
      window.localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({
          collapsed: Array.from(this._collapsed),
          removed: Array.from(this._removed),
        })
      );
    } catch (err) {
      /* localStorage 不可用时忽略，不影响功能 */
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._booted && hass) {
      this._booted = true;
      this._boot();
    }
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (this._hass && !this._booted) {
      this._booted = true;
      this._boot();
    }
  }

  async _boot() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
    await this._load();
  }

  // ---------------------------------------------------------------- 数据
  async _req(method, path, body) {
    const hass = this._hass;
    // 优先用 HA 自己的 callApi：它走前端已建立的鉴权通道，token 失效/刷新都不用管
    if (hass && typeof hass.callApi === "function") {
      try {
        const data = await hass.callApi(method, path, body);
        return data || {};
      } catch (err) {
        let msg = "";
        if (err && typeof err === "object") {
          msg = String(err.message || err.error || err.body || err.code || "");
        }
        throw new Error(msg || String(err) || "请求失败");
      }
    }
    // 兜底：个别面板实现没给 callApi
    const tok = hass && hass.auth && hass.auth.data ? hass.auth.data.access_token : null;
    if (!tok) throw new Error("未取到登录令牌，请刷新页面后重试");
    const res = await fetch(`/api/${path}`, {
      method,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = { error: text };
    }
    if (!res.ok) throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
    return data || {};
  }

  async _load() {
    this._busy = true;
    this._render();
    try {
      const data = await this._req("GET", "hdl_link/scan");
      this._scan = data && Array.isArray(data.devices) ? data : { devices: [], error: "返回结构异常" };
      this._pruneSelection();
      this._pruneRemoved();
    } catch (err) {
      this._scan = { devices: [], error: String(err && err.message ? err.message : err) };
    }
    this._busy = false;
    this._render();
  }

  // ---------------------------------------------------------------- 按键桥（面板 → HA）
  async _loadBridge() {
    this._bridgeBusy = true;
    this._bridgeError = "";
    this._render();
    try {
      const data = await this._req("GET", "hdl_link/bridge");
      this._bridge = data && Array.isArray(data.rows) ? data : null;
      if (!this._bridge) this._bridgeError = "返回结构异常";
    } catch (err) {
      this._bridgeError = String(err && err.message ? err.message : err);
    }
    this._bridgeBusy = false;
    this._render();
  }

  /** 行内的「绑定」：sid → HA 实体 */
  async _bindRow(sid, entityId, mode, action) {
    try {
      this._bridgeBusy = true;
      this._render();
      await this._req("POST", "hdl_link/bridge", {
        sid,
        entity_id: entityId,
        mode,
        action,
        trigger: "any",
      });
      await this._loadBridge();
    } catch (err) {
      this._bridgeBusy = false;
      this._bridgeError = String(err && err.message ? err.message : err);
      this._render();
    }
  }

  /** 行内「解绑」 */
  async _unbindRow(sid) {
    try {
      this._bridgeBusy = true;
      this._render();
      await this._req("POST", "hdl_link/bridge", { sid, entity_id: null });
      await this._loadBridge();
    } catch (err) {
      this._bridgeBusy = false;
      this._bridgeError = String(err && err.message ? err.message : err);
      this._render();
    }
  }

  /** 可绑定实体下拉数据（light/switch/cover/script…） */
  _entityIds() {
    const hass = this._hass;
    const states = hass && hass.states ? hass.states : {};
    const okDomains = [
      "light", "switch", "cover", "fan", "climate", "media_player", "script",
      "automation", "input_boolean", "scene", "button", "number", "lock", "vacuum",
    ];
    const ids = [];
    for (const id of Object.keys(states)) {
      const dot = id.indexOf(".");
      if (dot < 0) continue;
      if (!okDomains.includes(id.slice(0, dot))) continue;
      ids.push(id);
    }
    ids.sort();
    return ids;
  }

  _relTime(ts) {
    if (!ts) return "—";
    const diff = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)));
    if (diff < 60) return `${diff} 秒前`;
    if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
    return `${Math.round(diff / 86400)} 天前`;
  }

  /** 扫描结果变了以后，丢掉已经不存在的勾选 */
  _pruneSelection() {
    const alive = new Set();
    for (const d of this._devices()) {
      for (const f of d.functions || []) alive.add(f.entity_id);
    }
    for (const id of [...this._checked.keys()]) {
      if (!alive.has(id)) this._checked.delete(id);
    }
  }

  _devices() {
    const scan = this._scan;
    return scan && Array.isArray(scan.devices) ? scan.devices : [];
  }

  /** 扫描结果变了以后，丢掉已经不存在的「已移除」记录，避免恢复计数虚高 */
  _pruneRemoved() {
    if (!this._removed.size) return;
    const alive = new Set();
    for (const d of this._devices()) {
      for (const f of d.functions || []) alive.add(f.entity_id);
    }
    const kept = [...this._removed].filter((id) => alive.has(id));
    if (kept.length !== this._removed.size) {
      this._removed = new Set(kept);
      this._savePrefs();
    }
  }

  _visibleDevices() {
    return this._devices().filter((d) => {
      if (!this._showSystem && d.is_system) {
        // 系统实体默认隐藏，除非它真的有可写功能
        if (!d.supported_count) return false;
      }
      return this._matchedFunctions(d).length > 0;
    });
  }

  /** 搜索：设备命中就显示它全部功能；否则只显示功能名/entity_id 命中的行 */
  _matchedFunctions(device) {
    const fns = this._functionsOf(device);
    const q = this._query.trim().toLowerCase();
    if (!q) return fns;
    const hay = [device.name, device.model, device.manufacturer, device.area, device.integration]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (hay.includes(q)) return fns;
    return fns.filter(
      (f) => String(f.name || "").toLowerCase().includes(q) || String(f.entity_id || "").toLowerCase().includes(q)
    );
  }

  /** 该行是否「不可用」：HA 里没有状态、或 HDL 侧没有对应功能类型 */
  _isUnusable(fn) {
    const s = String(fn.state || "");
    return s === "unavailable" || s === "unknown" || !fn.supported;
  }

  _matchFilter(fn) {
    if (this._filter === "control") return fn.kind === "control" && fn.supported;
    if (this._filter === "sensor") return fn.kind === "sensor" && fn.supported;
    if (this._filter === "unsupported") return !fn.supported;
    return true;
  }

  _functionsOf(device) {
    return (device.functions || []).filter(
      (f) => this._matchFilter(f) && !this._removed.has(f.entity_id)
    );
  }

  _removedCount() {
    return this._removed.size;
  }

  _selectionPayload() {
    const devices = this._devices();
    const out = [];
    for (const d of devices) {
      const funcs = [];
      for (const f of d.functions || []) {
        const spk = this._checked.get(f.entity_id);
        if (!spk) continue;
        funcs.push({
          entity_id: f.entity_id,
          name: f.name,
          spk,
          attributes: f.attributes || [],
        });
      }
      if (!funcs.length) continue;
      out.push({
        device_id: d.device_id,
        name: d.name,
        model: d.model,
        functions: funcs,
      });
    }
    return out;
  }

  _counts() {
    let funcs = 0;
    let devs = 0;
    for (const d of this._devices()) {
      let n = 0;
      for (const f of d.functions || []) if (this._checked.has(f.entity_id)) n++;
      if (n) {
        devs++;
        funcs += n;
      }
    }
    return { devices: devs, functions: funcs };
  }

  // ---------------------------------------------------------------- 交互
  _toggleDevice(device, checked) {
    const fns = this._functionsOf(device).filter((f) => f.supported);
    for (const f of fns) {
      if (checked) this._checked.set(f.entity_id, f.suggested_spk || "");
      else this._checked.delete(f.entity_id);
    }
    this._render();
  }

  _toggleFunction(fn, checked, spk) {
    if (checked) this._checked.set(fn.entity_id, spk || fn.suggested_spk || "");
    else this._checked.delete(fn.entity_id);
    this._render();
  }

  _setSpk(entityId, spk) {
    if (this._checked.has(entityId)) this._checked.set(entityId, spk);
    this._render();
  }

  async _run() {
    const devices = this._selectionPayload();
    if (!devices.length) return;
    if (!this._dryRun) {
      const funcCount = devices.reduce((n, d) => n + ((d.functions || []).length), 0);
      const ok = await this._confirm(
        "确认写入 HDL 网关？",
        `<div style="line-height:1.8">
           即将向 <b>${escapeHtml(this._gatewayLabel || "HDL 网关")}</b> 写入
           <b style="color:#c62828">${devices.length}</b> 个物理模块、
           <b style="color:#c62828">${funcCount}</b> 个功能点。
           <div style="margin-top:10px;color:#666;font-size:13px">
             写入后这些 HA 设备会以「HA」为名出现在 HDL 侧，可在 HDL 面板 / 场景 / 按键里绑定控制。
             重复写入同一条功能点会做幂等处理，不会重复创建。
           </div>
         </div>`
      );
      if (!ok) {
        this._result = { dry_run: true, plan: [], errors: ["已取消写入"] };
        this._render();
        return;
      }
    }
    this._busy = true;
    this._result = null;
    this._render();
    try {
      const res = await this._req("POST", "hdl_link/provision", {
        devices,
        dry_run: this._dryRun,
        confirm: !this._dryRun,
      });
      this._result = res || {};
    } catch (err) {
      this._result = { errors: [String(err && err.message ? err.message : String(err))] };
    }
    this._busy = false;
    this._render();
  }

  /** 面板内二次确认弹窗（不依赖浏览器原生 confirm） */
  _confirm(title, html) {
    return new Promise((resolve) => {
      const mask = document.createElement("div");
      mask.style.cssText =
        "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99999;display:flex;" +
        "align-items:center;justify-content:center;font-family:inherit";
      mask.innerHTML = `
        <div style="background:#fff;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.3);
                    width:min(460px,90vw);padding:22px 24px">
          <div style="font-size:17px;font-weight:600;margin-bottom:12px">${escapeHtml(title)}</div>
          <div style="font-size:14px;color:#333">${html}</div>
          <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px">
            <button id="cn" style="padding:8px 16px;border-radius:8px;border:1px solid #ddd;
                    background:#fff;cursor:pointer;font-size:14px">取消</button>
            <button id="ok" style="padding:8px 16px;border-radius:8px;border:0;
                    background:#2563eb;color:#fff;cursor:pointer;font-size:14px;font-weight:600">确认写入</button>
          </div>
        </div>`;
      this.shadowRoot.appendChild(mask);
      const done = (val) => {
        mask.remove();
        resolve(val);
      };
      mask.querySelector("#cn").addEventListener("click", () => done(false));
      mask.querySelector("#ok").addEventListener("click", () => done(true));
      mask.addEventListener("click", (e) => {
        if (e.target === mask) done(false);
      });
    });
  }

  async _showToast(msg) {
    this.shadowRoot.querySelectorAll(".hint").forEach((n) => n.remove());
  }

  // ---------------------------------------------------------------- 渲染
  _render() {
    if (this._renderQueued) return;
    this._renderQueued = true;
    queueMicrotask(() => {
      this._renderQueued = false;
      try {
        this._renderInner();
      } catch (err) {
        if (this.shadowRoot) {
          this.shadowRoot.innerHTML = `<div style="padding:24px;color:#c62828;font-family:sans-serif">
            面板渲染失败：${escapeHtml(err && err.message ? err.message : String(err))}</div>`;
        }
      }
    });
  }

  _tabsStyle() {
    return `<style>
      .tabs { display:flex; gap:6px; margin-bottom:16px; border-bottom:1px solid var(--divider-color,#dcdfe3); }
      .tab { padding:9px 16px; font-size:14px; cursor:pointer; border:none; background:transparent;
             color:var(--secondary-text-color,#6b6b70); border-bottom:2px solid transparent; margin-bottom:-1px; }
      .tab[data-active="1"] { color:var(--primary-color,#03a9f4); font-weight:600; border-bottom-color:var(--primary-color,#03a9f4); }
    </style>`;
  }

  _tabsHtml() {
    return `${this._tabsStyle()}
      <div class="tabs">
        <button class="tab" data-tab="provision" data-active="${this._tab === "provision" ? 1 : 0}">设备同步</button>
        <button class="tab" data-tab="bridge" data-active="${this._tab === "bridge" ? 1 : 0}">按键桥（面板 → HA）</button>
      </div>`;
  }

  _renderInner() {
    if (this._tab === "bridge") {
      this._renderBridgeInner();
      return;
    }
    const counts = this._counts();
    const scan = this._scan;
    const devices = this._visibleDevices();
    const allCollapsed =
      devices.length > 0 && devices.every((d) => this._collapsed.has(d.device_id));

    const style = `
      <style>
        :host { display: block; }
        .wrap { padding: 16px 20px 96px; font-size: 14px; color: var(--primary-text-color, #1c1c1e); }
        .head { display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap; }
        .head h2 { margin:0 0 4px; font-size: 20px; font-weight:600; }
        .head p { margin:0; color: var(--secondary-text-color,#6b6b70); font-size:13px; line-height:1.5; max-width:640px; }
        .head .spacer { flex:1; }
        .btn { border:1px solid var(--divider-color,#dcdfe3); background:var(--card-background-color,#fff);
               color:var(--primary-text-color,#1c1c1e); border-radius:8px; padding:8px 14px; font-size:13px; cursor:pointer; }
        .btn:hover { background:var(--secondary-background-color,#f5f6f7); }
        .btn.primary { background:var(--primary-color,#03a9f4); border-color:transparent; color:#fff; font-weight:600; }
        .btn.primary[disabled] { opacity:.5; cursor:not-allowed; }
        .overview { margin:16px 0 12px; display:flex; gap:20px; flex-wrap:wrap; font-size:13px; color:var(--secondary-text-color,#6b6b70); }
        .overview b { color:var(--primary-text-color,#1c1c1e); font-size:15px; }
        .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
        .search { flex:1; min-width:220px; display:flex; align-items:center; gap:8px;
                  border:1px solid var(--divider-color,#dcdfe3); border-radius:8px; padding:6px 10px; background:var(--card-background-color,#fff); }
        .search input { border:none; outline:none; font-size:13px; width:100%; background:transparent; color:var(--primary-text-color,#1c1c1e); }
        .chips { display:flex; gap:6px; }
        .chip { border:1px solid var(--divider-color,#dcdfe3); border-radius:999px; padding:5px 12px; font-size:12px;
                cursor:pointer; background:var(--card-background-color,#fff); color:var(--secondary-text-color,#6b6b70); }
        .chip[data-active="1"] { border-color:var(--primary-color,#03a9f4); color:var(--primary-color,#03a9f4); font-weight:600; }
        .toggle { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--secondary-text-color,#6b6b70); cursor:pointer; }
        .card { border:1px solid var(--divider-color,#dcdfe3); border-radius:10px; margin-bottom:12px; overflow:hidden;
                background:var(--card-background-color,#fff); }
        .card[data-sel="1"] { border-color:var(--primary-color,#03a9f4); }
        .card-head { display:flex; align-items:center; gap:10px; padding:10px 14px; background:var(--secondary-background-color,#f5f6f7); }
        .card-head .name { font-weight:600; font-size:15px; cursor:pointer; }
        .chev { width:16px; text-align:center; cursor:pointer; color:var(--secondary-text-color,#6b6b70); font-size:13px; user-select:none; }
        .iconbtn { border:none; background:transparent; color:var(--secondary-text-color,#9aa0a6); cursor:pointer;
                   font-size:13px; line-height:1; padding:2px 6px; border-radius:4px; }
        .iconbtn:hover { background:#fdecec; color:#c62828; }
        tr.unusable td { opacity:.72; }
        .chip.restore { border-color:#f5d9a8; background:#fffaf0; color:#a45c11; }
        .hidden { display:none; }
        .card-head .name { font-weight:600; font-size:15px; }
        .tag { font-size:11px; padding:2px 8px; border-radius:999px; background:var(--card-background-color,#fff);
               border:1px solid var(--divider-color,#dcdfe3); color:var(--secondary-text-color,#6b6b70); }
        .tag.area { border-color:transparent; background:#e8f4fe; color:#0277bd; }
        .tag.done { border-color:transparent; background:#e6f4ea; color:#1e7b34; font-weight:600; }
        .lockval { font-size:12px; color:var(--secondary-text-color,#6b6b70); }
        tr.existing td { background:#fafbfc; }
        .card-head .meta { color:var(--secondary-text-color,#6b6b70); font-size:12px; }
        .card-head .right { margin-left:auto; font-size:12px; color:var(--secondary-text-color,#6b6b70); }
        .rows { width:100%; border-collapse:collapse; }
        .rows th { text-align:left; font-weight:500; font-size:12px; color:var(--secondary-text-color,#6b6b70); padding:8px 10px; border-bottom:1px solid var(--divider-color,#eef0f2); }
        .rows td { padding:8px 10px; border-bottom:1px solid var(--divider-color,#f2f4f6); vertical-align:middle; }
        .rows tr:last-child td { border-bottom:none; }
        .fn-name { font-size:13px; }
        .fn-sub { display:block; font-size:11px; color:var(--secondary-text-color,#9aa0a6); margin-top:2px; }
        .state { font-size:12px; padding:2px 8px; border-radius:4px; background:var(--secondary-background-color,#f2f4f6); white-space:nowrap; }
        .state.on { background:#e6f4ea; color:#1e7b34; }
        .state.off { background:#f1f3f4; color:#5f6368; }
        .state.muted { background:#fdf0e6; color:#a45c11; }
        .state.value { background:#eef1fb; color:#33459b; }
        .select { font-size:12px; padding:4px 6px; border-radius:6px; border:1px solid var(--divider-color,#dcdfe3);
                  background:var(--card-background-color,#fff); color:var(--primary-text-color,#1c1c1e); }
        .unsupported { font-size:12px; color:#a45c11; }
        .empty { border:1px dashed var(--divider-color,#dcdfe3); border-radius:10px; padding:28px; text-align:center;
                 color:var(--secondary-text-color,#6b6b70); font-size:13px; line-height:1.7; }
        .error { border:1px solid #f3c1c1; background:#fdf2f2; color:#a12626; border-radius:8px; padding:10px 14px;
                 font-size:13px; margin-bottom:12px; }
        .bar { position:fixed; left:0; right:0; bottom:0; display:flex; align-items:center; gap:14px; padding:12px 20px;
               background:var(--card-background-color,#fff); border-top:1px solid var(--divider-color,#dcdfe3); }
        .bar .sum { font-size:13px; }
        .bar .sum b { font-size:15px; }
        .bar .spacer { flex:1; }
        .result { margin-top:18px; }
        .result h3 { font-size:15px; margin:0 0 8px; }
        .result table { width:100%; border-collapse:collapse; font-size:12px; }
        .result th { text-align:left; color:var(--secondary-text-color,#6b6b70); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--divider-color,#eef0f2); }
        .result td { padding:6px 8px; border-bottom:1px solid var(--divider-color,#f4f6f8); }
        .ok { color:#1e7b34; font-weight:600; }
        .warn { color:#a45c11; font-weight:600; }
        .bad { color:#a12626; font-weight:600; }
        .note { font-size:12px; color:var(--secondary-text-color,#6b6b70); }
        .notice { border:1px solid #f5d9a8; background:#fffaf0; border-radius:8px; padding:10px 14px; margin-bottom:14px; font-size:13px; }
        .notice ul { margin:6px 0 0 18px; padding:0; }
        .notice li { font-size:12px; color:var(--secondary-text-color,#6b6b70); line-height:1.7; }
      </style>`;

    let body = "";
    if (this._busy && !scan) {
      body = `<div class="empty">正在扫描 HA 设备…</div>`;
    } else if (scan && scan.error) {
      body = `<div class="error">扫描失败：${escapeHtml(scan.error)}<br>请确认 HDL LINK 集成已加载，再点右上角「重新扫描」。</div>`;
    } else if (devices.length === 0) {
      body = `<div class="empty">
        没有找到可同步的设备<br>
        <span class="note">只包含 HA 里真实设备的可控功能与常用传感器；HDL 自建实体和系统虚拟实体已自动排除。</span>
      </div>`;
    } else {
      body = devices.map((d) => this._deviceCard(d)).join("");
    }

    const resultHtml = this._result ? this._resultBlock(this._result) : "";
    const counts2 = counts;
    const hasSel = counts2.functions > 0;

    this.shadowRoot.innerHTML = `${style}
      <div class="wrap">
        ${this._tabsHtml()}
        <div class="head">
          <div>
            <h2>HDL 设备同步</h2>
            <p>把 Home Assistant 里的设备注册到 HDL 网关：每台设备生成一个物理模型，每个功能生成一个功能点，之后可在 HDL 面板、场景、按键里调用。</p>
          </div>
          <div class="spacer"></div>
          <button class="btn" id="rescan" ${this._busy ? "disabled" : ""}>重新扫描</button>
        </div>

        <div class="overview">
          <span>已在网关 <b>${scan && scan.existing_device_count != null ? scan.existing_device_count : 0}</b> 台</span>
          <span>可新增设备 <b>${(scan && scan.device_count != null ? scan.device_count : 0) - (scan && scan.existing_device_count != null ? scan.existing_device_count : 0)}</b> 台</span>
          <span>可写入功能 <b>${scan && scan.supported_count != null ? scan.supported_count : 0}</b> 个</span>
          <span>本次已勾选 <b>${counts2.devices}</b> 台 · <b>${counts2.functions}</b> 个功能</span>
        </div>

        ${this._duplicateBlock(scan)}

        <div class="toolbar">
          <div class="search">
            <span>搜索</span>
            <input id="q" placeholder="设备名 / 型号 / 区域 / 功能名" value="${escapeHtml(this._query)}">
          </div>
          <div class="chips">
            <div class="chip" data-filter="all" data-active="${this._filter === "all" ? 1 : 0}">全部</div>
            <div class="chip" data-filter="control" data-active="${this._filter === "control" ? 1 : 0}">可控功能</div>
            <div class="chip" data-filter="sensor" data-active="${this._filter === "sensor" ? 1 : 0}">传感器</div>
            <div class="chip" data-filter="unsupported" data-active="${this._filter === "unsupported" ? 1 : 0}">暂不支持</div>
          </div>
          <label class="toggle"><input type="checkbox" id="sys" ${this._showSystem ? "checked" : ""}> 显示系统实体</label>
          <div class="spacer"></div>
          <div class="chip${this._removedCount() ? " restore" : " hidden"}" id="restore"
            title="恢复被手动移除的不可用功能">已移除 ${this._removedCount()} 个 · 恢复</div>
          <div class="chip" id="collapseAll">${allCollapsed ? "全部展开" : "全部收起"}</div>
        </div>

        ${body}
        ${resultHtml}
      </div>

      <div class="bar">
        <label class="toggle"><input type="checkbox" id="dry" ${this._dryRun ? "checked" : ""}> 先预览，不写网关</label>
        <span class="note" id="dryTip">${this._dryRun ? "只生成写入计划，便于核对 oid / 功能类型" : "点击后会弹窗二次确认，确认后才真正写入网关"}</span>
        <div class="spacer"></div>
        <span class="sum">已选 <b>${counts2.devices}</b> 台 · <b>${counts2.functions}</b> 个功能</span>
        <button class="btn primary" id="run" ${hasSel && !this._busy ? "" : "disabled"}>
          ${this._busy ? "处理中…" : this._dryRun ? "预览写入计划" : "写入 HDL 网关"}
        </button>
      </div>`;

    this._bind();
  }

  // ---------------------------------------------------------------- 按键桥页签
  _renderBridgeInner() {
    const data = this._bridge;
    const counts = (data && data.counts) || { total: 0, scenes: 0, functions: 0, bound: 0, unbound: 0, fired: 0 };
    const q = (this._bridgeQuery || "").trim().toLowerCase();
    const all = (data && data.rows) || [];
    const rows = all.filter(
      (r) =>
        !q ||
        String(r.name || "").toLowerCase().includes(q) ||
        String(r.sid || "").toLowerCase().includes(q) ||
        String(r.entity_id || "").toLowerCase().includes(q)
    );
    const orphan = (data && data.orphan_bindings) || [];
    const entityOptions = this._entityIds()
      .map((id) => `<option value="${escapeHtml(id)}"></option>`)
      .join("");

    const sceneTable = this._bridgeTable(rows.filter((r) => r.source === "scene"), "场景");
    const fnTable = this._bridgeTable(rows.filter((r) => r.source === "function"), "功能点（真实设备回路/开关）");

    const body = !data
      ? `<div class="empty">${this._bridgeBusy ? "正在读取网关场景与功能点…" : "还没有数据，点右上角「刷新」。"}</div>`
      : rows.length === 0
      ? `<div class="empty">没有匹配的场景/功能点。<span class="note">网关里没有场景时，先在 HDL App 建一个场景并把面板键绑上去。</span></div>`
      : `${sceneTable}${fnTable}`;

    const style = `
      <style>
        :host { display: block; }
        .wrap { padding: 16px 20px 40px; font-size: 14px; color: var(--primary-text-color,#1c1c1e); }
        .head { display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap; }
        .head h2 { margin:0 0 4px; font-size:20px; font-weight:600; }
        .head p { margin:0; color:var(--secondary-text-color,#6b6b70); font-size:13px; line-height:1.6; max-width:760px; }
        .head .spacer { flex:1; }
        .btn { border:1px solid var(--divider-color,#dcdfe3); background:var(--card-background-color,#fff);
               color:var(--primary-text-color,#1c1c1e); border-radius:8px; padding:8px 14px; font-size:13px; cursor:pointer; }
        .btn:hover { background:var(--secondary-background-color,#f5f6f7); }
        .btn.primary { background:var(--primary-color,#03a9f4); border-color:transparent; color:#fff; font-weight:600; }
        .btn[disabled] { opacity:.5; cursor:not-allowed; }
        .btn.mini { padding:4px 10px; font-size:12px; border-radius:6px; }
        .overview { margin:16px 0 12px; display:flex; gap:20px; flex-wrap:wrap; font-size:13px; color:var(--secondary-text-color,#6b6b70); }
        .overview b { color:var(--primary-text-color,#1c1c1e); font-size:15px; }
        .notice { border:1px solid #cfe3f7; background:#f5faff; border-radius:8px; padding:12px 14px; margin-bottom:14px; font-size:13px; line-height:1.8; }
        .notice b { color:#0277bd; }
        .notice ol { margin:6px 0 0 18px; padding:0; }
        .notice li { font-size:12px; color:var(--secondary-text-color,#4a4a4d); line-height:1.8; }
        .error { border:1px solid #f3c1c1; background:#fdf2f2; color:#a12626; border-radius:8px; padding:10px 14px; font-size:13px; margin-bottom:12px; }
        .search { display:flex; align-items:center; gap:8px; margin-bottom:12px; border:1px solid var(--divider-color,#dcdfe3);
                  border-radius:8px; padding:6px 10px; background:var(--card-background-color,#fff); max-width:520px; }
        .search input { border:none; outline:none; font-size:13px; width:100%; background:transparent; color:var(--primary-text-color,#1c1c1e); }
        .card { border:1px solid var(--divider-color,#dcdfe3); border-radius:10px; margin-bottom:14px; overflow:hidden; background:var(--card-background-color,#fff); }
        .card-head { padding:10px 14px; background:var(--secondary-background-color,#f5f6f7); font-weight:600; font-size:14px; }
        .rows { width:100%; border-collapse:collapse; }
        .rows th { text-align:left; font-weight:500; font-size:12px; color:var(--secondary-text-color,#6b6b70); padding:8px 10px; border-bottom:1px solid var(--divider-color,#eef0f2); }
        .rows td { padding:8px 10px; border-bottom:1px solid var(--divider-color,#f2f4f6); vertical-align:middle; }
        .rows tr:last-child td { border-bottom:none; }
        tr[data-bound="1"] td { background:#f6fbf7; }
        .fn-name { font-size:13px; font-weight:500; }
        .fn-sub { display:block; font-size:11px; color:var(--secondary-text-color,#9aa0a6); margin-top:2px; }
        .state { font-size:12px; padding:2px 8px; border-radius:4px; background:var(--secondary-background-color,#f2f4f6); white-space:nowrap; }
        .state.on { background:#e6f4ea; color:#1e7b34; }
        .state.off { background:#f1f3f4; color:#5f6368; }
        .tag { font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--divider-color,#dcdfe3); color:var(--secondary-text-color,#6b6b70); }
        .tag.done { border-color:transparent; background:#e6f4ea; color:#1e7b34; font-weight:600; }
        .tag.area { border-color:transparent; background:#e8f4fe; color:#0277bd; }
        .ent { width:100%; box-sizing:border-box; font-size:12px; padding:5px 7px; border-radius:6px;
               border:1px solid var(--divider-color,#dcdfe3); background:var(--card-background-color,#fff); color:var(--primary-text-color,#1c1c1e); }
        .select { font-size:12px; padding:4px 6px; border-radius:6px; border:1px solid var(--divider-color,#dcdfe3);
                  background:var(--card-background-color,#fff); color:var(--primary-text-color,#1c1c1e); }
        .empty { border:1px dashed var(--divider-color,#dcdfe3); border-radius:10px; padding:28px; text-align:center;
                 color:var(--secondary-text-color,#6b6b70); font-size:13px; line-height:1.7; }
        .note { font-size:12px; color:var(--secondary-text-color,#6b6b70); }
        .hit { font-size:12px; }
        .hit.none { color:#a45c11; }
      </style>`;

    this.shadowRoot.innerHTML = `${style}
      <div class="wrap">
        ${this._tabsHtml()}
        <div class="head">
          <div>
            <h2>按键桥 · 用 HDL 面板控制 HA 设备</h2>
            <p>面板按键在网关内部由设备驱动执行，第三方设备没有 HDL 驱动，所以「按键 → HA 实体」不能直连。
               可行且稳定的做法是事件桥：按键触发网关会广播的事件（场景执行 / 回路状态变化），HA 侧收到事件后执行动作。</p>
          </div>
          <div class="spacer"></div>
          <button class="btn" id="bRefresh" ${this._bridgeBusy ? "disabled" : ""}>${this._bridgeBusy ? "读取中…" : "刷新"}</button>
        </div>

        <div class="notice">
          <b>三步落地</b>（第 1、2 步在 HDL App 里做，LINK 协议不提供按键配置接口）：
          <ol>
            <li>HDL App 新建场景（建议命名 <b>HA:客厅灯</b>，需要 HA 侧动作时场景本身可以留空），把面板键绑到该场景；</li>
            <li>回到本页点「刷新」，找到这个场景，在右侧填目标实体并点「绑定」；</li>
            <li>按一下面板键：本页该行的「最近事件」变成「刚刚 / 几秒前」，同时 HA 实体状态变化。</li>
          </ol>
          <div class="note">若按了键「最近事件」仍是「—」，说明事件没到 HA：先确认面板键确实指向该场景（HDL App 里看），而不是 HA 侧绑定问题。</div>
        </div>

        <div class="overview">
          <span>集成版本 <b>${escapeHtml((data && data.version) || "—")}</b></span>
          <span>场景 <b>${counts.scenes}</b></span>
          <span>真实功能点 <b>${counts.functions}</b></span>
          <span>已绑定 <b>${counts.bound}</b></span>
          <span>未绑定 <b>${counts.unbound}</b></span>
          <span>收到过事件 <b>${counts.fired}</b></span>
        </div>

        ${this._bridgeError ? `<div class="error">${escapeHtml(this._bridgeError)}</div>` : ""}
        ${
          orphan.length
            ? `<div class="error">有 ${orphan.length} 条绑定在网关里已找不到对应场景/功能点（多半是场景被改名或重建）：
                ${orphan.map((o) => `${escapeHtml(o.sid)} → ${escapeHtml(o.entity_id || "")}`).join("；")}</div>`
            : ""
        }

        <div class="search">
          <span>搜索</span>
          <input id="bq" placeholder="场景名 / 功能点名称 / SID / 实体" value="${escapeHtml(this._bridgeQuery)}">
        </div>

        <datalist id="hdl-ents">${entityOptions}</datalist>
        ${body}
      </div>`;

    this._bindBridge();
  }

  _bridgeTable(rows, title) {
    if (!rows.length) return "";
    const trs = rows
      .map((r) => {
        const sid = String(r.sid || "");
        const mode = r.mode || "action";
        const action = r.action || "toggle";
        const on = r.status === "on" || r.status === true;
        const statusTxt =
          r.source === "scene" ? (on ? "执行中" : "待机") : r.online === false ? "离线" : "在线";
        const actions = ["toggle", "turn_on", "turn_off", "open", "close", "run"]
          .map((a) => `<option value="${a}" ${a === action ? "selected" : ""}>${a}</option>`)
          .join("");
        const hit = r.count
          ? `<span class="hit">${escapeHtml(this._relTime(r.last_at))} · ${r.count} 次</span>`
          : `<span class="hit none">未收到过</span>`;
        return `<tr data-bound="${r.bound ? 1 : 0}" data-row="${escapeHtml(sid)}">
          <td>
            <span class="fn-name">${escapeHtml(r.name || sid)}</span>
            <span class="fn-sub">${escapeHtml(sid)}${
          r.omodel ? " · " + escapeHtml(r.omodel) : ""
        }</span>
          </td>
          <td style="width:90px"><span class="state ${on ? "on" : "off"}">${escapeHtml(statusTxt)}</span></td>
          <td style="width:230px">
            <input class="ent" list="hdl-ents" data-ent value="${escapeHtml(r.entity_id || "")}"
              placeholder="选择或输入 HA 实体">
          </td>
          <td style="width:110px">
            <select class="select" data-mode>
              <option value="action" ${mode === "action" ? "selected" : ""}>action</option>
              <option value="follow" ${mode === "follow" ? "selected" : ""}>follow</option>
            </select>
          </td>
          <td style="width:110px">
            <select class="select" data-act>${actions}</select>
          </td>
          <td style="width:120px">${hit}</td>
          <td style="width:130px;text-align:right;white-space:nowrap">
            <button class="btn mini primary" data-bind ${this._bridgeBusy ? "disabled" : ""}>绑定</button>
            ${r.bound ? `<button class="btn mini" data-unbind ${this._bridgeBusy ? "disabled" : ""}>解绑</button>` : ""}
          </td>
        </tr>`;
      })
      .join("");
    return `<div class="card">
      <div class="card-head">${escapeHtml(title)} · ${rows.length} 项</div>
      <table class="rows">
        <thead><tr>
          <th>名称 / SID</th><th>状态</th><th>绑定的 HA 实体</th><th>模式</th><th>动作</th><th>最近事件</th><th></th>
        </tr></thead>
        <tbody>${trs}</tbody>
      </table>
    </div>`;
  }

  _bindBridge() {
    this._bindTabs();
    const root = this.shadowRoot;
    if (!root) return;

    const refresh = root.querySelector("#bRefresh");
    if (refresh) refresh.addEventListener("click", () => this._loadBridge());

    const bq = root.querySelector("#bq");
    if (bq) {
      bq.addEventListener("input", (e) => {
        this._bridgeQuery = e.target.value;
        this._render();
        const box = this.shadowRoot.querySelector("#bq");
        if (box) {
          box.focus();
          box.setSelectionRange(box.value.length, box.value.length);
        }
      });
    }

    root.querySelectorAll("button[data-bind]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tr = btn.closest("tr");
        const sid = tr && tr.dataset ? tr.dataset.row : "";
        if (!sid) return;
        const ent = tr.querySelector("input[data-ent]");
        const mode = tr.querySelector("select[data-mode]");
        const act = tr.querySelector("select[data-act]");
        const entityId = ent ? ent.value.trim() : "";
        if (!entityId) {
          this._bridgeError = "请先填写要绑定的 HA 实体（如 light.keting_deng）";
          this._render();
          return;
        }
        this._bindRow(sid, entityId, mode ? mode.value : "action", act ? act.value : "toggle");
      });
    });

    root.querySelectorAll("button[data-unbind]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tr = btn.closest("tr");
        const sid = tr && tr.dataset ? tr.dataset.row : "";
        if (sid) this._unbindRow(sid);
      });
    });
  }

  _deviceCard(device) {
    const fns = this._matchedFunctions(device);
    if (!fns.length) return "";

    const isExisting = !!device.existing;
    const collapsed = this._collapsed.has(device.device_id);
    const selected = isExisting
      ? fns.length
      : fns.filter((f) => this._checked.has(f.entity_id)).length;
    const allSupported = fns.filter((f) => f.supported);
    const allOn = isExisting || (allSupported.length > 0 && allSupported.every((f) => this._checked.has(f.entity_id)));

    const metaParts = [device.manufacturer, device.model].filter(Boolean).join(" · ");

    const rows = fns
      .map((f) => {
        const on = isExisting || this._checked.has(f.entity_id);
        const currentSpk = this._checked.get(f.entity_id) || f.suggested_spk || "";
        const shortName = this._shortName(f.name, device.name);
        let select;
        if (isExisting) {
          select = `<span class="lockval">${escapeHtml(SPK_LABELS[currentSpk] || f.spk_label || "—")}</span>`;
        } else if (f.spk_options && f.spk_options.length) {
          select = `<select class="select" data-spk-for="${escapeHtml(f.entity_id)}" ${on ? "" : "disabled"}>
              ${f.spk_options
                .map(
                  (o) =>
                    `<option value="${escapeHtml(o.id)}" ${o.id === currentSpk ? "selected" : ""}>${escapeHtml(o.label)}</option>`
                )
                .join("")}
             </select>`;
        } else {
          select = `<span class="unsupported">该功能暂无对应的 HDL 类型</span>`;
        }
        const unusable = this._isUnusable(f);
        return `<tr class="${isExisting ? "existing" : ""}${unusable ? " unusable" : ""}">
          <td style="width:34px">
            <input type="checkbox" data-entity="${escapeHtml(f.entity_id)}"
              ${on ? "checked" : ""} ${isExisting || !f.supported ? "disabled" : ""}
              title="${isExisting ? "该功能已在 HDL 网关中" : ""}">
          </td>
          <td>
            <span class="fn-name">${escapeHtml(shortName)}</span>
            <span class="fn-sub">${escapeHtml(DOMAIN_LABEL[f.domain] || f.domain)} · ${escapeHtml(f.entity_id)}</span>
          </td>
          <td style="width:150px"><span class="state ${stateTone(f)}">${escapeHtml(stateText(f))}</span></td>
          <td style="width:170px">${select}</td>
          <td style="width:44px;text-align:right">${
            unusable
              ? `<button class="iconbtn" data-remove="${escapeHtml(f.entity_id)}" title="从列表移除（只影响本页展示）">✕</button>`
              : ""
          }</td>
        </tr>`;
      })
      .join("");

    return `<div class="card" data-sel="${selected && !isExisting ? 1 : 0}" data-card="${escapeHtml(device.device_id)}">
      <div class="card-head">
        <span class="chev" data-collapse="${escapeHtml(device.device_id)}" title="${collapsed ? "展开功能列表" : "收起功能列表"}">${collapsed ? "▸" : "▾"}</span>
        <input type="checkbox" data-device="${escapeHtml(device.device_id)}"
          ${allOn ? "checked" : ""} ${isExisting || !allSupported.length ? "disabled" : ""}>
        <span class="name" data-collapse="${escapeHtml(device.device_id)}">${escapeHtml(device.name)}</span>
        ${isExisting ? `<span class="tag done">已在 HDL 网关</span>` : ""}
        ${device.area ? `<span class="tag area">${escapeHtml(device.area)}</span>` : ""}
        ${device.integration ? `<span class="tag">${escapeHtml(device.integration)}</span>` : ""}
        ${metaParts ? `<span class="meta">${escapeHtml(metaParts)}</span>` : ""}
        <span class="right">${
          isExisting
            ? `${fns.length} 个功能 · 只读`
            : `${device.supported_count} 个可写入 · 已选 ${selected}`
        }${collapsed ? " · 已收起" : ""}</span>
      </div>
      ${collapsed
        ? ""
        : `<table class="rows">
        <thead><tr>
          <th></th><th>功能</th><th>当前状态</th><th>写成 HDL 的功能</th><th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`}
    </div>`;
  }

  /** 同名功能提示：帮用户看出「重复」到底重复在哪 */
  _duplicateBlock(scan) {
    if (!scan || !scan.duplicate_groups) return "";
    const list = Array.isArray(scan.duplicates) ? scan.duplicates : [];
    const items = list
      .slice(0, 6)
      .map(
        (d) =>
          `<li>${escapeHtml(d.device)} 下的「${escapeHtml(d.name)}」有 ${d.entities.length} 个功能重名（${escapeHtml(
            (d.entities || []).slice(0, 4).join(", ")
          )}${d.entities.length > 4 ? " …" : ""}）</li>`
      )
      .join("");
    return `<div class="notice">
      <b>检测到 ${scan.duplicate_groups} 组重名功能</b>
      <div class="note">这些功能在 HDL 工程里没有单独命名（或混入了别的工程），导入后实体名称会看起来重复。改名要在 HDL Studio / 网关工程里做，集成侧不会自动改。</div>
      <ul>${items}</ul>
    </div>`;
  }

  _shortName(name, deviceName) {
    if (!name) return "";
    if (deviceName && name.startsWith(deviceName)) {
      const rest = name.slice(deviceName.length).trim();
      if (rest) return rest;
    }
    return name;
  }

  _resultBlock(res) {
    const plan = Array.isArray(res.plan) ? res.plan : [];
    const errors = Array.isArray(res.errors) ? res.errors : [];
    const warnings = Array.isArray(res.warnings) ? res.warnings : [];
    if (!plan.length && !errors.length) {
      return `<div class="result"><h3>结果</h3><div class="note">无内容</div></div>`;
    }

    const rows = [];
    for (const dev of plan) {
      const devName = dev.name || dev.device_id || "";
      const oid = dev.oid || "（待网关分配）";
      const funcs = Array.isArray(dev.functions) ? dev.functions : [];
      if (!funcs.length) {
        rows.push(`<tr><td>${escapeHtml(devName)}</td><td colspan="3" class="note">—</td>
          <td colspan="2" class="warn">设备未建立</td></tr>`);
        continue;
      }
      for (const fn of funcs) {
        let stateTxt = "计划";
        let cls = "note";
        if (!res.dry_run) {
          if (fn.ok) {
            stateTxt = "已写入";
            cls = "ok";
          } else if (fn.note && fn.note.indexOf("跳过") >= 0) {
            stateTxt = "跳过";
            cls = "note";
          } else {
            stateTxt = "未生效";
            cls = "warn";
          }
        }
        rows.push(`<tr>
          <td>${escapeHtml(devName)}</td>
          <td>${escapeHtml(fn.name || fn.entity_id || "")}</td>
          <td>${escapeHtml(SPK_LABELS[fn.spk] || fn.spk || "")}</td>
          <td>${escapeHtml(oid)}</td>
          <td class="${cls}">${stateTxt}</td>
          <td class="note">${escapeHtml(fn.note || "")}</td>
        </tr>`);
      }
    }

    return `<div class="result">
      <h3>${res.dry_run ? "写入计划（未写入网关）" : "写入结果"}</h3>
      ${errors.length ? `<div class="error">${errors.map((e) => escapeHtml(e)).join("<br>")}</div>` : ""}
      ${warnings.length ? `<div class="note">${warnings.map((w) => escapeHtml(w)).join("<br>")}</div>` : ""}
      <table>
        <thead><tr><th>设备</th><th>功能</th><th>HDL 类型</th><th>oid</th><th>结果</th><th>说明</th></tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>`;
  }

  _bindTabs() {
    const root = this.shadowRoot;
    if (!root) return;
    root.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const next = tab.dataset.tab;
        if (!next || next === this._tab) return;
        this._tab = next;
        this._render();
        if (next === "bridge" && !this._bridge && !this._bridgeBusy) this._loadBridge();
      });
    });
  }

  _bind() {
    this._bindTabs();
    const root = this.shadowRoot;
    if (!root) return;

    const rescan = root.querySelector("#rescan");
    if (rescan) rescan.addEventListener("click", () => this._load());

    const q = root.querySelector("#q");
    if (q) {
      q.addEventListener("input", (e) => {
        this._query = e.target.value;
        this._render();
        const box = this.shadowRoot.querySelector("#q");
        if (box) {
          box.focus();
          box.setSelectionRange(box.value.length, box.value.length);
        }
      });
    }

    root.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        this._filter = chip.dataset.filter;
        this._render();
      });
    });

    const sys = root.querySelector("#sys");
    if (sys) sys.addEventListener("change", (e) => {
      this._showSystem = e.target.checked;
      this._render();
    });

    const dry = root.querySelector("#dry");
    if (dry) dry.addEventListener("change", (e) => {
      this._dryRun = e.target.checked;
      this._render();
    });

    const run = root.querySelector("#run");
    if (run) run.addEventListener("click", () => this._run());

    const visible = this._visibleDevices();
    root.querySelectorAll("input[data-device]").forEach((box) => {
      const deviceId = box.dataset.device;
      const device = visible.find((d) => d.device_id === deviceId);
      box.addEventListener("change", (e) => {
        if (device) this._toggleDevice(device, e.target.checked);
      });
    });

    root.querySelectorAll("input[data-entity]").forEach((box) => {
      const entityId = box.dataset.entity;
      const fn = visible.flatMap((d) => d.functions || []).find((f) => f.entity_id === entityId);
      box.addEventListener("change", (e) => {
        if (fn) this._toggleFunction(fn, e.target.checked, fn.suggested_spk);
      });
    });

    root.querySelectorAll("select[data-spk-for]").forEach((sel) => {
      sel.addEventListener("change", (e) => this._setSpk(e.target.dataset.spkFor, e.target.value));
    });

    // 收起 / 展开设备
    root.querySelectorAll("[data-collapse]").forEach((node) => {
      node.addEventListener("click", (e) => {
        e.preventDefault();
        const id = node.dataset.collapse;
        if (this._collapsed.has(id)) this._collapsed.delete(id);
        else this._collapsed.add(id);
        this._savePrefs();
        this._render();
      });
    });

    // 移除不可用功能（只影响本页展示，可用「恢复」找回）
    root.querySelectorAll("[data-remove]").forEach((node) => {
      node.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._removed.add(node.dataset.remove);
        this._checked.delete(node.dataset.remove);
        this._savePrefs();
        this._render();
      });
    });

    const restore = root.querySelector("#restore");
    if (restore) restore.addEventListener("click", () => {
      if (!this._removed.size) return;
      this._removed.clear();
      this._savePrefs();
      this._render();
    });

    const collapseAll = root.querySelector("#collapseAll");
    if (collapseAll) collapseAll.addEventListener("click", () => {
      const visible = this._visibleDevices();
      const allCollapsed = visible.length > 0 && visible.every((d) => this._collapsed.has(d.device_id));
      for (const d of visible) {
        if (allCollapsed) this._collapsed.delete(d.device_id);
        else this._collapsed.add(d.device_id);
      }
      this._savePrefs();
      this._render();
    });
  }
}

// 面板会被重复加载（切页/刷新），标签名只能注册一次，否则报
// "hdl-link-provision has already been used with this registry"
if (!customElements.get("hdl-link-provision")) {
  customElements.define("hdl-link-provision", HdlLinkProvision);
}
