/* HDL LINK 设备同步面板：扫描 HA 设备 -> 勾选 -> 写入 HDL 网关 */
const HDL_SPK_TITLES = {
  "light.switch": "灯（开关）",
  "light.dimming": "灯（调光）",
  "light.cct": "灯（调光+色温）",
  "light.rgb": "灯（RGB）",
  "light.rgbw": "灯（RGBW）",
  "panel.socket": "插座",
  "other.common": "通用开关",
  "curtain.switch": "窗帘（开关停）",
  "curtain.trietex": "开合帘",
  "curtain.roller": "卷帘",
  "sensor.temperature": "温度",
  "sensor.humidity": "湿度",
  "sensor.light": "照度",
  "sensor.co2": "CO2",
  "sensor.pm25": "PM2.5",
  "sensor.pir": "人体感应",
  "sensor.doorwindow": "门磁",
};

class HdlLinkProvision extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._scan = null;
    this._selected = new Map(); // device_id -> Set(entity_id)
    this._spk = new Map();      // entity_id -> spk
    this._busy = false;
    this._result = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._boot();
  }

  get hass() {
    return this._hass;
  }

  async _boot() {
    try {
      this._render();
      await this._load();
    } catch (err) {
      this._scan = { error: "面板初始化失败：" + (err && err.message ? err.message : String(err)) };
      this._render();
    }
  }

  async _req(method, path, body) {
    const hass = this._hass;
    const tok = hass && hass.auth && hass.auth.data ? hass.auth.data.access_token : null;
    if (tok) {
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
      if (!res.ok) {
        throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
      }
      return data || {};
    }
    return hass.callApi(method, path, body);
  }

  async _load() {
    this._busy = true;
    this._render();
    try {
      const data = await this._req("GET", "hdl_link/scan");
      this._scan = data;
    } catch (err) {
      this._scan = { error: String(err && err.message ? err.message : err) };
    }
    this._busy = false;
    this._render();
  }

  _toggleDevice(device, on) {
    const set = new Set();
    if (on) {
      device.functions.forEach((f) => set.add(f.entity_id));
    }
    this._selected.set(device.device_id, set);
    this._render();
  }

  _toggleFunction(device, entityId, on) {
    if (!this._selected.has(device.device_id)) this._selected.set(device.device_id, new Set());
    const set = this._selected.get(device.device_id);
    if (on) set.add(entityId);
    else set.delete(entityId);
    this._render();
  }

  _selectionPayload() {
    if (!this._scan || !Array.isArray(this._scan.devices)) return [];
    const devices = [];
    this._scan.devices.forEach((device) => {
      const sel = this._selected.get(device.device_id);
      if (!sel || sel.size === 0) return;
      const functions = device.functions
        .filter((f) => sel.has(f.entity_id))
        .map((f) => ({
          entity_id: f.entity_id,
          name: f.name,
          spk: this._spk.get(f.entity_id) || f.suggested_spk || "",
          attributes: f.attributes || [],
        }));
      devices.push({
        device_id: device.device_id,
        name: device.name,
        model: device.model,
        functions,
      });
    });
    return devices;
  }

  async _submit() {
    const devices = this._selectionPayload();
    if (devices.length === 0) {
      this._result = { error: "请至少勾选一个功能" };
      this._render();
      return;
    }
    const dryRun = this.shadowRoot.querySelector("#dryrun").checked;
    this._busy = true;
    this._render();
    try {
      const res = await this._req("POST", "hdl_link/provision", {
        devices,
        dry_run: dryRun,
      });
      this._result = res;
    } catch (err) {
      this._result = { error: String(err && err.message ? err.message : err) };
    }
    this._busy = false;
    this._render();
  }

  _render() {
    try {
      this._renderInner();
    } catch (err) {
      const msg = "面板渲染失败：" + (err && err.message ? err.message : String(err));
      this.shadowRoot.innerHTML =
        `<div style="padding:24px;font-family:sans-serif;color:#c62828">${escapeHtml(msg)}</div>`;
      console.error("[hdl-link-provision]", err);
    }
  }

  _renderInner() {
    const sr = this.shadowRoot;
    const scrollTop = sr.querySelector("#list") ? sr.querySelector("#list").scrollTop : 0;
    const scan = this._scan;
    let header = "";
    let body = "";

    if (this._busy && !scan) {
      body = `<div class="empty">正在扫描 HA 设备…</div>`;
    } else if (!scan) {
      body = `<div class="empty">尚未扫描</div>`;
    } else if (scan.error) {
      body = `<div class="empty err">扫描失败：${escapeHtml(scan.error)}</div>`;
    } else if (scan.device_count === 0) {
      body = `<div class="empty">没有找到可同步的设备</div>`;
    } else {
      header = `<div class="stats">共 <b>${scan.device_count}</b> 台设备、<b>${scan.function_count}</b> 个功能（已排除 HDL 自有设备 ${scan.skipped_hdl_devices} 台）</div>`;
      body = scan.devices.map((device) => {
        const sel = this._selected.get(device.device_id) || new Set();
        const allOn = sel.size === device.functions.length && device.functions.length > 0;
        const rows = device.functions
          .map((f) => {
            const spk = this._spk.get(f.entity_id) || f.suggested_spk || "";
            const opts = (f.spk_options.length ? f.spk_options : [spk])
              .filter(Boolean)
              .map(
                (o) =>
                  `<option value="${o}" ${o === spk ? "selected" : ""}>${HDL_SPK_TITLES[o] || o}</option>`
              )
              .join("");
            return `<tr>
              <td class="cb"><input type="checkbox" data-dev="${device.device_id}" data-ent="${f.entity_id}" ${sel.has(f.entity_id) ? "checked" : ""}></td>
              <td><span class="fname">${escapeHtml(f.name)}</span><div class="eid">${escapeHtml(f.entity_id)}</div></td>
              <td class="dom">${f.domain}</td>
              <td class="st">${escapeHtml(String(f.state))}</td>
              <td class="spk"><select data-ent="${f.entity_id}">${opts}</select></td>
            </tr>`;
          })
          .join("");
        return `<div class="dev">
          <div class="devhead">
            <input type="checkbox" class="devcb" data-dev="${device.device_id}" ${allOn ? "checked" : ""}>
            <span class="dname">${escapeHtml(device.name)}</span>
            <span class="tag">${device.functions.length} 个功能</span>
            <span class="meta">${escapeHtml(device.integration || "")}${device.area ? " · " + escapeHtml(device.area) : ""}${device.model ? " · " + escapeHtml(device.model) : ""}</span>
            <span class="status ${sel.size ? "on" : ""}">${sel.size} 已选</span>
          </div>
          <table>
            <thead><tr><th></th><th>功能</th><th>类型</th><th>状态</th><th>HDL 功能类型</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
      }).join("");
    }

    const totalSelected = this._selectionPayload().reduce((n, d) => n + d.functions.length, 0);
    const resultHtml = this._result ? this._resultHtml() : "";
    const selCount = this._selectedCount();

    sr.innerHTML = `
      <style>
        :host { display:block; font-family: var(--paper-font-body1_-_font-family, sans-serif); color: var(--primary-text-color); }
        .bar { display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid var(--divider-color); position:sticky; top:0; background: var(--card-background-color); z-index:2; flex-wrap:wrap; }
        .bar h2 { margin:0; font-size:18px; font-weight:600; }
        button { border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); border-radius:8px; padding:8px 14px; font-size:14px; cursor:pointer; }
        button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
        button:disabled { opacity:.5; cursor:default; }
        .stats { padding:8px 16px; font-size:13px; color: var(--secondary-text-color); }
        #list { max-height: calc(100vh - 190px); overflow:auto; padding: 0 16px 16px; }
        .dev { border:1px solid var(--divider-color); border-radius:10px; margin-bottom:12px; overflow:hidden; }
        .devhead { display:flex; align-items:center; gap:10px; padding:10px 12px; background: var(--secondary-background-color, #f5f5f5); }
        .dname { font-weight:600; font-size:15px; }
        .tag { font-size:12px; padding:2px 8px; border-radius:10px; background: var(--primary-color); color:#fff; }
        .meta { font-size:12px; color: var(--secondary-text-color); flex:1; }
        .status { font-size:12px; color: var(--secondary-text-color); }
        .status.on { color: var(--primary-color); font-weight:600; }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        th { text-align:left; padding:6px 10px; color: var(--secondary-text-color); font-weight:500; border-bottom:1px solid var(--divider-color); }
        td { padding:6px 10px; border-bottom:1px solid var(--divider-color); vertical-align:middle; }
        td.cb { width:28px; }
        .fname { font-size:13px; }
        .eid { font-size:11px; color: var(--secondary-text-color); }
        .st, .dom { font-size:12px; color: var(--secondary-text-color); }
        select { padding:5px 8px; border-radius:6px; border:1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); font-size:13px; }
        .empty { padding:40px; text-align:center; color: var(--secondary-text-color); }
        .empty.err { color: var(--error-color, #c62828); }
        .foot { display:flex; align-items:center; gap:12px; padding:12px 16px; border-top:1px solid var(--divider-color); position:sticky; bottom:0; background: var(--card-background-color); }
        .foot label { font-size:13px; color: var(--secondary-text-color); display:flex; align-items:center; gap:6px; }
        .result { margin:0 16px 16px; padding:12px; border-radius:10px; background: var(--secondary-background-color,#f5f5f5); font-size:13px; white-space:pre-wrap; }
        .result.plan { border:1px solid var(--warning-color, #ff9800); }
      </style>
      <div class="bar">
        <h2>HDL 设备同步</h2>
        <button id="reload" ${this._busy ? "disabled" : ""}>重新扫描</button>
        <span style="flex:1"></span>
        <span class="stats">已勾选 ${selCount} 个功能 / ${totalSelected}</span>
        <button class="primary" id="submit" ${this._busy || !selCount ? "disabled" : ""}>添加到 HDL 网关</button>
      </div>
      ${header}
      <div id="list">${body}</div>
      <div class="foot">
        <label><input type="checkbox" id="dryrun" checked> 先演练（不写网关，只看计划）</label>
        <span style="flex:1"></span>
        ${this._busy ? "<span class='stats'>处理中…</span>" : ""}
      </div>
      ${resultHtml}
    `;

    sr.querySelector("#reload").onclick = () => this._load();
    sr.querySelector("#submit").onclick = () => this._submit();
    const devList = (scan && Array.isArray(scan.devices)) ? scan.devices : [];
    sr.querySelectorAll("input.devcb").forEach((el) => {
      el.onchange = () => {
        const device = devList.find((d) => d.device_id === el.dataset.dev);
        if (device) this._toggleDevice(device, el.checked);
      };
    });
    sr.querySelectorAll("input[data-ent]").forEach((el) => {
      el.onchange = () => {
        const device = devList.find((d) => d.device_id === el.dataset.dev);
        if (device) this._toggleFunction(device, el.dataset.ent, el.checked);
      };
    });
    sr.querySelectorAll("select[data-ent]").forEach((el) => {
      el.onchange = () => {
        this._spk.set(el.dataset.ent, el.value);
      };
    });
    const list = sr.querySelector("#list");
    if (list) list.scrollTop = scrollTop;
  }

  _selectedCount() {
    let n = 0;
    this._selected.forEach((set) => (n += set.size));
    return n;
  }

  _resultHtml() {
    const r = this._result;
    if (!r) return "";
    if (r.error) return `<div class="result">失败：${escapeHtml(r.error)}</div>`;
    const lines = [];
    lines.push(`模式：${r.dry_run ? "演练（未写入网关）" : "已写入网关"}`);
    lines.push(`新增设备：${r.created_devices}　新增功能：${r.created_functions}`);
    (r.plan || []).forEach((d) => {
      lines.push("");
      lines.push(`设备 ${d.name} → oid ${d.oid || "(未取到)"}`);
      (d.functions || []).forEach((f) => {
        lines.push(`　${f.ok ? "✓" : "✗"} sid ${f.sid}　${f.name}　${f.spk}${f.note ? "　" + f.note : ""}`);
      });
    });
    if ((r.warnings || []).length) {
      lines.push("");
      lines.push("提醒：");
      r.warnings.forEach((w) => lines.push("· " + w));
    }
    if ((r.errors || []).length) {
      lines.push("");
      lines.push("错误：");
      r.errors.forEach((e) => lines.push("· " + e));
    }
    return `<div class="result${r.dry_run ? " plan" : ""}">${escapeHtml(lines.join("\n"))}</div>`;
  }
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

customElements.define("hdl-link-provision", HdlLinkProvision);
