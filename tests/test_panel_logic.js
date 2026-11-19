/**
 * 面板 JS 逻辑自测（不依赖 DOM）：搜索匹配、移除不可用功能、收起状态持久化。
 * 运行：node tests/test_panel_logic.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const candidates = [
  path.join(__dirname, "..", "custom_components", "hdl_link", "frontend", "provision.js"),
  path.join(__dirname, "hdl_link-integration", "custom_components", "hdl_link", "frontend", "provision.js"),
];
const FILE = candidates.find((p) => fs.existsSync(p));
if (!FILE) {
  console.error("找不到 provision.js，检查路径：", candidates);
  process.exit(1);
}

const store = {};
const sandbox = {
  HTMLElement: class {},
  customElements: { define() {} },
  window: {
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => {
        store[k] = String(v);
      },
    },
  },
  queueMicrotask: (fn) => fn(),
  document: {},
  console,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(FILE, "utf8") + "\n;globalThis.__Hdl = HdlLinkProvision;",
  sandbox,
  { filename: FILE }
);
const Hdl = sandbox.__Hdl;

const failures = [];
function check(name, cond, extra = "") {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${extra ? `  [${extra}]` : ""}`);
  if (!cond) failures.push(name);
}

function makePanel(scan) {
  const p = new Hdl();
  p._scan = scan;
  return p;
}

const SCAN = {
  devices: [
    {
      device_id: "dev_light",
      name: "厨房灯",
      model: "MQTT-1",
      manufacturer: "Maker",
      area: "一楼",
      integration: "MQTT",
      is_system: false,
      existing: false,
      supported_count: 2,
      functions: [
        { entity_id: "light.kitchen", name: "厨房灯", domain: "light", kind: "control", supported: true, state: "on", suggested_spk: "light.dimming", spk_options: [{ id: "light.dimming", label: "调光灯" }], attributes: [] },
        { entity_id: "sensor.kitchen_temp", name: "厨房温度", domain: "sensor", kind: "sensor", supported: true, state: "23.5", unit: "°C", suggested_spk: "sensor.temperature", spk_options: [], attributes: [] },
        { entity_id: "light.kitchen_old", name: "旧灯", domain: "light", kind: "control", supported: false, state: "unavailable", suggested_spk: "", spk_options: [], attributes: [] },
      ],
    },
    {
      device_id: "dev_hdl",
      name: "HDL 功能模块",
      model: "MGWIP",
      manufacturer: "HDL",
      area: "一楼",
      integration: "HDL LINK",
      is_system: false,
      existing: true,
      supported_count: 1,
      functions: [
        { entity_id: "light.channel_08", name: "通道 08", domain: "light", kind: "control", supported: true, state: "on", suggested_spk: "light.switch", spk_options: [], attributes: [] },
      ],
    },
  ],
};

// 1. 搜索：设备名命中 → 显示该设备全部功能
{
  const p = makePanel(SCAN);
  p._query = "厨房灯";
  check("搜索设备名命中，返回全部 3 行", p._matchedFunctions(SCAN.devices[0]).length === 3);
  check("搜索时另一台设备不命中", p._matchedFunctions(SCAN.devices[1]).length === 0);
}

// 2. 搜索：功能名命中 → 只返回命中行
{
  const p = makePanel(SCAN);
  p._query = "温度";
  const rows = p._matchedFunctions(SCAN.devices[0]);
  check("搜索功能名「温度」只返回 1 行", rows.length === 1, rows.map((r) => r.name).join(","));
  check("命中的是温度传感器", rows[0].entity_id === "sensor.kitchen_temp");
}

// 3. 搜索：entity_id 命中
{
  const p = makePanel(SCAN);
  p._query = "kitchen_old";
  check("搜索 entity_id 命中", p._matchedFunctions(SCAN.devices[0]).length === 1);
}

// 4. 不可用判定
{
  const p = makePanel(SCAN);
  const rows = SCAN.devices[0].functions;
  check("unavailable 判为不可用", p._isUnusable(rows[2]) === true);
  check("supported=false 判为不可用", p._isUnusable({ state: "on", supported: false }) === true);
  check("正常可控功能不算不可用", p._isUnusable(rows[0]) === false);
  check("正常传感器不算不可用", p._isUnusable(rows[1]) === false);
}

// 5. 移除不可用功能
{
  const p = makePanel(SCAN);
  p._removed.add("light.kitchen_old");
  check("移除后该行不再出现", p._functionsOf(SCAN.devices[0]).length === 2);
  check("移除计数为 1", p._removedCount() === 1);
  p._removed.add("light.kitchen");
  p._removed.add("sensor.kitchen_temp");
  check("一台设备功能全移除后，设备不在可见列表", p._visibleDevices().some((d) => d.device_id === "dev_light") === false);
  check("仍保留 HDL 已有设备", p._visibleDevices().some((d) => d.device_id === "dev_hdl") === true);
}

// 6. 恢复
{
  const p = makePanel(SCAN);
  p._removed.add("light.kitchen_old");
  p._removed.clear();
  check("恢复后行数回到 3", p._functionsOf(SCAN.devices[0]).length === 3);
}

// 7. 收起状态与移除记录持久化
{
  const p1 = makePanel(SCAN);
  p1._collapsed.add("dev_light");
  p1._removed.add("light.kitchen_old");
  p1._savePrefs();
  const p2 = makePanel(SCAN);
  check("新建面板恢复收起状态", p2._collapsed.has("dev_light"));
  check("新建面板恢复移除记录", p2._removed.has("light.kitchen_old"));
  const p3 = makePanel(SCAN);
  p3._pruneRemoved();
  check("扫描结果里已消失的移除记录被清掉",
    (() => { p3._removed.add("light.gone"); p3._pruneRemoved(); return !p3._removed.has("light.gone"); })());
}

// 8. 选择计数不把已移除的功能算进去
{
  const p = makePanel(SCAN);
  p._checked.set("light.kitchen_old", "light.switch");
  p._removed.add("light.kitchen_old");
  const counts = p._counts();
  check("已移除功能不计入已选数量", counts.functions === 1, `functions=${counts.functions}`);
}

console.log("\n结果：", failures.length === 0 ? "全部通过" : `${failures.length} 项失败 ${failures}`);
process.exit(failures.length === 0 ? 0 : 1);
