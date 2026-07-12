const coords = {
  S: [10, 48],
  P1: [28, 22],
  P2: [28, 58],
  P3: [56, 38],
  A: [72, 14],
  B: [86, 38],
  C: [78, 60],
};

const edges = [
  ["S", "P1"], ["S", "P2"], ["P1", "P3"], ["P2", "P3"],
  ["P1", "A"], ["P3", "B"], ["P3", "C"],
];

const $ = (id) => document.getElementById(id);
const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

let selectedTarget = null;
let activeModule = "overview";
let backendOnline = true;
let logFilter = "all";
let lastQrValue = "-";
let lastQrMatched = null;
let clientLogs = [];
let lastClientLog = "";
let statusCache = {};

const defaultStatus = {
  auth: false,
  user: "未认证",
  car_connected: true,
  mode: "AUTH_REQUIRED",
  mode_text: "等待人脸认证",
  target: null,
  task_type: "delivery",
  task_status: "waiting",
  path: [],
  cost: null,
  blocked: [],
  path_status: "未规划",
  distance_cm: 42,
  obstacle: false,
  line_bits: [0, 1, 1, 0],
  line_state: "CENTER",
  strategy: "正常行驶",
  rgb_color: "blue",
  buzzer: "off",
  vision_mode: "人脸识别",
  vision_result: "等待识别",
  voice_listening: false,
  voice_command: "未启动",
  logs: [],
};

const moduleMeta = {
  overview: ["系统总览", "任务调度总览", "按验收流程查看认证、路径、巡线、避障、校验和反馈闭环。"],
  auth: ["权限入口", "身份认证", "人脸识别是任务控制入口，未认证用户不能启动规划、巡线和手动控制。"],
  task: ["任务入口", "任务下发", "选择目标点和任务类型，向 RASPBOT-V2 下发配送或巡检任务。"],
  path: ["算法展示", "A* 路径规划", "展示节点地图、规划路径、总代价和可选障碍节点重规划。"],
  run: ["执行流程", "自动巡线", "路径准备完成后进入自动巡线，可暂停、继续或取消。"],
  safety: ["安全策略", "安全避障", "查看超声波距离、循迹状态、障碍物状态和停车策略。"],
  vision: ["多模态视觉", "视觉识别", "集中展示人脸、二维码、手势和语音指令识别结果。"],
  voice: ["语音交互", "语音控制", "文本模拟语音命令，或启动真实语音监听。支持天气、送餐、停止等 9 类指令。"],
  arrival: ["终点确认", "到达校验", "到达目标点后识别二维码，确认结果与任务目标一致。"],
  feedback: ["反馈闭环", "声光反馈", "RGB 与蜂鸣器随待机、执行、遇障和完成状态同步变化。"],
  manual: ["调试工具", "手动控制", "提供遥控器式底盘调试，自动任务中仅允许停止和急停。"],
  logs: ["过程留痕", "任务日志", "记录认证、规划、执行、避障、校验和完成全过程。"],
};

const targetInfo = {
  A: "器材区",
  B: "实验台",
  C: "返回区",
};

const taskTypeLabel = {
  delivery: "实验室物品配送",
  inspection: "教学楼巡检",
  return: "返回起点",
};

const taskStatusLabel = {
  waiting: "等待任务",
  ready: "路径就绪",
  running: "执行中",
  paused: "已暂停",
  error_stop: "遇障停车",
  obstacle_stop: "遇障停车",
  arrival_check: "到达校验",
  qr_error: "校验异常",
  done: "已完成",
  cancelled: "已取消",
};

const modeLabels = {
  AUTH_REQUIRED: "等待认证",
  READY: "路径就绪",
  LINE_FOLLOW: "自动巡线中",
  OBSTACLE_STOP: "遇障停车",
  ARRIVAL_CHECK: "到达校验",
  GESTURE_MODE: "手势控制",
  ERROR_STOP: "异常停止",
  DONE: "任务完成",
};

const lineStateLabels = {
  CENTER: "居中",
  "LEFT偏移": "左偏",
  LEFT: "左偏",
  "RIGHT偏移": "右偏",
  RIGHT: "右偏",
};

const logFilterLabels = {
  all: "全部",
  auth: "认证",
  path: "路径",
  run: "执行",
  error: "异常",
  done: "完成",
};

function logFilterLabel(key) {
  return logFilterLabels[key] || "全部";
}

function statusLabel(value) {
  return taskStatusLabel[value] || "未知状态";
}

function modeLabel(s) {
  const text = s.mode_text;
  if (taskStatusLabel[text] || modeLabels[text]) return taskStatusLabel[text] || modeLabels[text];
  if (text && !/^[A-Z_]+$|^[a-z_]+$/.test(text)) return text;
  return modeLabels[s.mode] || "未知状态";
}

function lineStateLabel(value) {
  return lineStateLabels[value] || "未知";
}

function pathText(path) {
  return path && path.length ? path.join(" → ") : "尚未生成路径";
}

function networkErrorMessage(error) {
  const message = error?.message || "";
  if (message.includes("Failed to fetch")) {
    return "后端连接失败，请检查 Flask 服务是否启动。";
  }
  return `后端连接失败：${message || "未知错误"}`;
}

function safeStatus() {
  return {
    ...defaultStatus,
    ...statusCache,
    path: Array.isArray(statusCache.path) ? statusCache.path : defaultStatus.path,
    blocked: Array.isArray(statusCache.blocked) ? statusCache.blocked : defaultStatus.blocked,
    line_bits: Array.isArray(statusCache.line_bits) ? statusCache.line_bits : defaultStatus.line_bits,
    logs: Array.isArray(statusCache.logs) ? statusCache.logs : defaultStatus.logs,
  };
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setHTML(id, value) {
  const el = $(id);
  if (el) el.innerHTML = value;
}

function setChip(el, text, type = "") {
  if (!el) return;
  el.textContent = text;
  el.className = `status-chip ${type}`.trim();
}

const lightLabels = {
  blue: "蓝灯",
  yellow: "黄灯",
  red: "红灯",
  green: "绿灯",
};

const buzzerLabels = {
  off: "关闭",
  alarm: "报警",
  beep: "提示音",
  on: "开启",
};

function lightLabel(color = "blue") {
  return lightLabels[color] || color || "未设置";
}

function buzzerLabel(value = "off") {
  return buzzerLabels[value] || value || "未设置";
}

function feedbackPlain(s) {
  return `${lightLabel(s.rgb_color)} · ${buzzerLabel(s.buzzer)}`;
}

function feedbackLinesHtml(color = "blue", buzzer = "off") {
  return `<div class="feedback-lines">
    <p><span>灯光</span><strong>${lightLabel(color)}</strong></p>
    <p><span>蜂鸣</span><strong>${buzzerLabel(buzzer)}</strong></p>
  </div>`;
}

function miniFeedbackHtml(color = "blue", buzzer = "off") {
  return `<div class="mini-feedback">
    <span>声光</span>
    <p><b>灯光</b><strong>${lightLabel(color)}</strong></p>
    <p><b>蜂鸣</b><strong>${buzzerLabel(buzzer)}</strong></p>
  </div>`;
}

function qrPlaceholderHtml() {
  const active = new Set([0, 1, 2, 7, 14, 8, 15, 16, 32, 39, 46, 40, 47, 48, 10, 12, 18, 22, 24, 30, 34, 36, 38, 44]);
  return `<div class="qr-placeholder" aria-hidden="true">
    ${Array.from({length: 49}).map((_, i) => `<i class="${active.has(i) ? "on" : ""}"></i>`).join("")}
  </div>`;
}

function addClientLog(message, level = "danger") {
  if (message === lastClientLog) return;
  lastClientLog = message;
  clientLogs.push({
    time: new Date().toLocaleTimeString("zh-CN", {hour12: false}),
    message,
    level,
  });
  clientLogs = clientLogs.slice(-12);
}

function modeIsRunning(s) {
  return s.mode === "LINE_FOLLOW";
}

function modeIsObstacle(s) {
  return s.mode === "OBSTACLE_STOP" || s.task_status === "obstacle_stop" || s.task_status === "error_stop" || s.obstacle;
}

function modeIsDone(s) {
  return s.mode === "DONE" || s.task_status === "done";
}

function hasUsablePath(s) {
  const target = currentTarget(s);
  return Boolean(target && s.path.length > 0 && (!s.target || s.target === target));
}

function currentTarget(s = safeStatus()) {
  return s.target || selectedTarget || null;
}

function targetLabel(target) {
  return target ? `${target} 点（${targetInfo[target] || "未知区域"}）` : "未选择";
}

function nextSuggestion(s) {
  if (!backendOnline) return "后端连接失败，请先确认 Flask 服务正在运行。";
  if (!s.auth) return "请先完成人脸识别，认证后才能规划路径和启动任务。";
  if (!currentTarget(s)) return "请选择 A、B、C 中的目标点，并生成 A* 路径。";
  if (!s.path.length) return "请生成 A* 路径，必要时选择 P1、P2、P3 作为障碍节点后重新规划。";
  if (modeIsObstacle(s)) return "小车已停车，确认障碍物移除后点击“障碍移除继续”。";
  if (modeIsRunning(s)) return "任务正在自动巡线，可观察循迹传感器或在异常时暂停任务。";
  if (s.mode === "ARRIVAL_CHECK") return "请进行二维码校验，确认当前位置与目标点一致。";
  if (modeIsDone(s)) return "任务已完成，可以重新选择目标点并开始下一次配送。";
  return "路径已准备好，可以开始巡线任务。";
}

function commandBlockReason(command, s) {
  if (!backendOnline) return "后端连接失败，控制按钮已禁用";
  // 直连控制命令无需认证
  if (["face", "faceFail", "logout", "clearLogs", "carLineFollow", "carAvoid", "carStop", "carPause", "carResume"].includes(command)) return "";
  if (!s.auth) return "请先完成人脸识别认证";
  if (["plan", "replan", "start", "voice"].includes(command) && !currentTarget(s)) return "请先选择目标点";
  if (command === "qr" && s.mode !== "ARRIVAL_CHECK" && !modeIsDone(s)) {
    return "请先到达目标点后再进行二维码校验";
  }
  if (command === "arrival" && !hasUsablePath(s)) {
    return "请先生成当前目标点的 A* 路径";
  }
  if (["plan", "replan", "voice", "gesture", "arrival"].includes(command)) return "";
  if (command === "start") {
    if (modeIsRunning(s)) return "自动巡线执行中";
    if (modeIsObstacle(s)) return "遇障停车中，请先移除障碍";
    if (!hasUsablePath(s)) return "请先生成当前目标点的 A* 路径";
  }
  if ((command === "pause" || command === "carPause") && !modeIsRunning(s)) return "只有自动巡线中才能暂停";
  if ((command === "resume" || command === "carResume") && !(modeIsObstacle(s) || s.task_status === "paused" || s.task_status === "error_stop" || s.task_status === "obstacle_stop" || s.mode === "READY")) {
    return "当前状态不需要继续任务";
  }
  if (command === "cancel" && s.task_status === "waiting") return "当前没有正在处理的任务";
  return "";
}

function taskActionButtons(s) {
  if (s.task_status === "error_stop" || modeIsObstacle(s)) {
    return `<button class="success-btn" data-command="resume">障碍移除继续</button><button class="ghost-btn" data-command="cancel">取消任务</button>`;
  }
  return `<button class="primary-btn" data-command="plan">生成 A* 路径</button><button class="success-btn" data-command="start">开始任务</button><button class="ghost-btn" data-command="cancel">取消任务</button>`;
}

function manualBlockReason(action, s) {
  if (!backendOnline) return "后端连接失败，手动控制已禁用";
  // 停止和急停无需认证
  if (action === "stop") return "";
  if (!s.auth) return "请先完成人脸识别认证";
  if (modeIsRunning(s) && action !== "stop") return "自动巡线中，仅允许停止或急停";
  return "";
}

function setDisabled(button, reason) {
  button.disabled = Boolean(reason);
  button.title = reason || "";
}

function applyControlState(s = safeStatus()) {
  qsa("[data-command]").forEach((button) => {
    setDisabled(button, commandBlockReason(button.dataset.command, s));
  });
  qsa("[data-manual]").forEach((button) => {
    setDisabled(button, manualBlockReason(button.dataset.manual, s));
  });
  const emergency = $("emergencyStop");
  if (emergency) setDisabled(emergency, backendOnline ? "" : "后端连接失败，急停接口不可用");
}

async function endpoint(url, body = {}) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    backendOnline = true;
    return await res.json();
  } catch (error) {
    handleOffline(networkErrorMessage(error));
    return {success: false, message: "后端连接失败，请确认 Flask 服务已启动"};
  }
}

function enterConsole(moduleName = "overview") {
  setActiveModule(moduleName);
}

function setActiveModule(moduleName) {
  activeModule = moduleName;
  document.body.dataset.module = moduleName;
  qsa(".module-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.module === moduleName);
  });
  renderWorkspace();
  updateAllViews(safeStatus());
}

function targetButtons() {
  const activeTarget = currentTarget();
  return `<div class="target-row">
    ${["A", "B", "C"].map((target) => `
      <button data-target="${target}" class="${activeTarget === target ? "active" : ""}">
        <strong>${target} 点</strong>
        <small>${targetInfo[target]}</small>
      </button>`).join("")}
  </div>`;
}

function routeMapSvg() {
  return `<svg viewBox="0 0 100 70" aria-label="A* 路径地图" data-map-svg>
    ${edges.map(([a, b]) => {
      const [x1, y1] = coords[a];
      const [x2, y2] = coords[b];
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"></line>`;
    }).join("")}
    <polyline data-path-line points=""></polyline>
    <g data-map-nodes></g>
  </svg>`;
}

function stepState(key, s) {
  if (key === "auth") return s.auth ? ["已完成", "done"] : ["未完成", "active"];
  if (key === "task") return s.target ? [`目标：${s.target}`, "done"] : ["未完成", s.auth ? "active" : "todo"];
  if (key === "path") return s.path.length ? ["已完成", "done"] : ["未完成", s.target ? "active" : "todo"];
  if (key === "run") {
    if (modeIsObstacle(s) || s.mode === "ERROR_STOP") return ["异常", "error"];
    if (modeIsRunning(s)) return ["执行中", "running"];
    if (modeIsDone(s)) return ["已完成", "done"];
    return ["未完成", s.path.length ? "active" : "todo"];
  }
  if (key === "safety") return modeIsObstacle(s) ? ["异常", "error"] : ["正常", s.auth ? "done" : "todo"];
  if (key === "vision") return s.vision_result === "等待识别" ? ["未完成", "todo"] : ["已完成", "done"];
  if (key === "arrival") {
    if (modeIsDone(s)) return ["已完成", "done"];
    if (s.mode === "ARRIVAL_CHECK") return ["执行中", "running"];
    return ["未完成", "todo"];
  }
  if (key === "feedback") return modeIsDone(s) ? ["已完成", "done"] : [feedbackPlain(s), "todo"];
  return ["未完成", "todo"];
}

function renderOverview(s) {
  const steps = [
    ["auth", "身份认证", "人脸识别权限入口"],
    ["task", "任务下发", "选择配送目标"],
    ["path", "A* 规划", "生成可行路径"],
    ["run", "自动巡线", "沿路径执行任务"],
    ["safety", "安全避障", "超声波与循迹保护"],
    ["vision", "视觉识别", "识别二维码与指令"],
    ["arrival", "到达校验", "二维码目标确认"],
    ["feedback", "声光反馈", "RGB 与蜂鸣器"],
  ];

  return `
    <div class="overview-layout">
      <div class="overview-grid">
        ${steps.map(([key, title, desc], index) => {
          const [label, state] = stepState(key, s);
          return `<button class="process-card status-${state}" data-open-module="${key}" data-step-card="${key}">
            <span>${String(index + 1).padStart(2, "0")}</span>
            <strong>${title}</strong>
            <em>${desc}</em>
            <b data-step-status="${key}">${label}</b>
          </button>`;
        }).join("")}
      </div>
      <section class="next-action">
        <span>下一步建议</span>
        <strong id="nextSuggestion">${nextSuggestion(s)}</strong>
      </section>
      <div class="action-row">
        <button class="primary-btn" data-command="plan">生成路径</button>
        <button class="success-btn" data-command="start">开始任务</button>
        <button class="warning-btn" data-command="obstacle">模拟遇障</button>
        <button class="ghost-btn" data-command="arrival">模拟到达</button>
      </div>
      <div class="overview-bottom">
        <section class="summary-box">
          <span>当前阶段</span>
          <strong id="overviewModeText">${modeLabel(s)}</strong>
        </section>
        <section class="summary-box">
          <span>目标点</span>
          <strong id="overviewTargetText">${targetLabel(currentTarget(s))}</strong>
        </section>
        <section class="summary-box">
          <span>路径状态</span>
          <strong id="overviewPathText">${pathText(s.path)}</strong>
        </section>
        <section class="summary-box">
          <span>安全状态</span>
          <strong id="overviewSafetyText">${s.strategy}</strong>
        </section>
      </div>
    </div>`;
}

function renderModuleBody(name, s) {
  const route = pathText(s.path);
  const target = currentTarget(s);
  const taskBlocked = s.task_status === "error_stop" || modeIsObstacle(s);
  const taskActions = taskActionButtons(s);
  const modules = {
    overview: renderOverview(s),
    auth: `
      <div class="module-split">
        <div class="camera-frame auth-camera">
          <div class="scan-line"></div>
          <div class="face-target"><span></span></div>
          <div class="camera-caption"><strong>身份核验</strong><span id="authCameraResult">${s.vision_result}</span></div>
        </div>
        <div class="module-panel compact-panel">
          <h3>身份认证操作</h3>
          <p>通过摄像头完成人脸权限校验。认证成功后才允许规划路径、启动任务和手动控制。</p>
          <div class="action-row">
            <button class="primary-btn" data-command="face">开始人脸识别</button>
            <button class="warning-btn" data-command="faceFail">模拟识别失败</button>
            <button class="ghost-btn" data-command="logout">退出认证</button>
          </div>
        </div>
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>认证状态</span><strong id="authStateText">${s.auth ? "已认证" : "未认证"}</strong></section>
        <section class="summary-box"><span>当前用户</span><strong id="authUserText">${s.user}</strong></section>
        <section class="summary-box"><span>识别结果</span><strong id="authVisionText">${s.vision_result}</strong></section>
        <section class="summary-box feedback-state-card"><span>声光反馈</span><div id="authFeedbackText">${feedbackLinesHtml(s.rgb_color, s.buzzer)}</div></section>
      </div>`,
    task: `
      <div class="module-panel">
        <div class="task-layout">
          <div class="task-config">
            <h3>选择目标点</h3>
            <p>选择配送或巡检目标后，系统将生成 A* 路径。</p>
            <ul class="target-hint">
              <li>A 点：器材区</li>
              <li>B 点：实验台</li>
              <li>C 点：返回区</li>
            </ul>
            ${targetButtons()}
            <label for="taskType">任务类型</label>
            <select id="taskType">
              <option value="delivery">实验室物品配送</option>
              <option value="inspection">教学楼巡检</option>
              <option value="return">返回起点</option>
            </select>
          </div>
          <aside class="task-summary-card">
            <span>当前任务</span>
            <strong id="taskTypeText">${taskTypeLabel[s.task_type] || "实验室物品配送"}</strong>
            <span>目标点</span>
            <strong id="taskTargetText">${targetLabel(target)}</strong>
            <span>任务状态</span>
            <strong id="taskStatusText">${statusLabel(s.task_status)}</strong>
            <p class="task-alert" id="taskAlert" ${taskBlocked ? "" : "hidden"}>检测到障碍物，小车已停车。请先移除障碍，再继续任务。</p>
            <div class="action-row" id="taskActionRow">${taskActions}</div>
          </aside>
        </div>
      </div>`,
    path: `
      <div class="module-split">
        <div class="module-panel">
          <h3>A* 节点地图</h3>
          <div class="route-map module-route-map">${routeMapSvg()}</div>
          <p class="route-pill" id="pathRouteText">${route}</p>
        </div>
        <div class="module-panel">
          <h3>规划参数</h3>
          <div class="summary-grid two">
            <div><span>起点</span><strong>S</strong></div>
            <div><span>目标</span><strong id="pathTargetText">${target || "未选择"}</strong></div>
            <div><span>障碍节点</span><strong id="blockedText">${s.blocked.length ? s.blocked.join("、") : "无"}</strong></div>
            <div><span>总代价</span><strong id="pathCostText">${s.cost ?? "-"}</strong></div>
          </div>
          <label>重规划障碍节点</label>
          <div class="check-row">
            ${["P1", "P2", "P3"].map((node) => `<label><input type="checkbox" data-block-node="${node}" ${s.blocked.includes(node) ? "checked" : ""}> ${node}</label>`).join("")}
          </div>
          <div class="action-row">
            <button class="primary-btn" data-command="plan">规划路径</button>
            <button class="warning-btn" data-command="replan">按所选障碍重规划</button>
          </div>
        </div>
      </div>`,
    run: `
      <div class="run-stage" id="runStage">
        <div class="moving-car"></div>
        <span id="runStageText">等待巡线</span>
      </div>
      <div class="progress-track"><i id="taskProgressBar"></i></div>
      <div class="action-row">
        <button class="success-btn" data-command="start">开始巡线</button>
        <button class="primary-btn" data-command="carLineFollow">⚡ 直连循迹</button>
        <button class="ghost-btn" data-command="carPause">⏸ 暂停</button>
        <button class="primary-btn" data-command="carResume">▶ 继续</button>
        <button class="warning-btn" data-command="cancel">取消</button>
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>当前模式</span><strong id="runModeText">${modeLabel(s)}</strong></section>
        <section class="summary-box"><span>当前节点</span><strong id="currentNodeText">S</strong></section>
        <section class="summary-box"><span>下一节点</span><strong id="nextNodeText">-</strong></section>
        <section class="summary-box"><span>任务进度</span><strong id="progressText">0%</strong></section>
        <section class="summary-box wide-summary"><span>当前路径</span><strong id="runPathText">${route}</strong></section>
        <section class="summary-box"><span>循迹传感器</span><strong id="runLineBitsText">${s.line_bits.join(" ")}</strong></section>
      </div>`,
    safety: `
      <div class="safety-readout" id="safetyReadout"><strong id="safetyDistanceText">${s.distance_cm}</strong><span>cm</span></div>
      <div class="threshold-row">
        <div class="safe">安全：&gt; 25 cm</div>
        <div class="warn">警告：15～25 cm</div>
        <div class="danger">危险：&lt; 15 cm</div>
      </div>
      <p class="state-note" id="obstacleNote">${modeIsObstacle(s) ? "已停车，等待障碍移除。" : "当前距离处于安全范围。"}</p>
      <div class="action-row">
        <button class="primary-btn" data-command="carAvoid">⚡ 绕障测试</button>
        <button class="warning-btn" data-command="obstacle">模拟遇障停车</button>
        <button class="success-btn" data-command="resume">障碍移除继续</button>
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>障碍物</span><strong id="safetyObstacleText">${s.obstacle ? "检测到障碍物" : "安全"}</strong></section>
        <section class="summary-box"><span>循迹传感器</span><strong id="safetyLineBitsText">${s.line_bits.join(" ")}</strong></section>
        <section class="summary-box"><span>巡线状态</span><strong id="safetyLineStateText">${lineStateLabel(s.line_state)}</strong></section>
        <section class="summary-box"><span>安全策略</span><strong id="safetyStrategyText">${s.strategy}</strong></section>
      </div>`,
    vision: `
      <div class="vision-grid">
        ${visionCard("face", "人脸识别", "识别授权用户", "face", "开始人脸识别")}
        ${visionCard("qr", "二维码识别", "目标点二维码校验", "qr", "启动二维码识别")}
        ${visionCard("gesture", "手势识别", "摄像头手势控制", "gesture", "启动手势识别")}
        ${visionCard("voice", "语音指令", target ? `语音：去 ${target} 点` : "语音：请选择目标", "voice", target ? `语音：去 ${target} 点` : "语音：请选择目标")}
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>视觉模式</span><strong id="visionModeText">${s.vision_mode}</strong></section>
        <section class="summary-box"><span>当前结果</span><strong id="visionResultText">${s.vision_result}</strong></section>
        <section class="summary-box"><span>语音目标</span><strong id="voiceTargetText">${target ? `去 ${target} 点` : "未选择目标"}</strong></section>
        <section class="summary-box feedback-state-card"><span>声光反馈</span><div id="visionFeedbackText">${feedbackLinesHtml(s.rgb_color, s.buzzer)}</div></section>
      </div>`,
    arrival: `
      <div class="module-split">
        <div class="qr-stage">
          <div class="qr-card">
            ${qrPlaceholderHtml()}
            <div class="qr-target-info">
              <span>当前目标</span>
              <strong id="arrivalTargetBig">${target ? `${target} 点` : "未选择"}</strong>
            </div>
          </div>
        </div>
        <div class="module-panel">
          <h3>到达校验</h3>
          <div class="summary-grid two">
            <div><span>当前目标点</span><strong id="arrivalTargetText">${targetLabel(target)}</strong></div>
            <div><span>识别二维码</span><strong id="qrValueText">${lastQrValue}</strong></div>
            <div><span>校验结果</span><strong id="qrMatchText">${lastQrMatched === true ? "一致" : lastQrMatched === false ? "不一致" : "待校验"}</strong></div>
            <div><span>任务状态</span><strong id="arrivalStatusText">${statusLabel(s.task_status)}</strong></div>
          </div>
          <div class="action-row">
            <button class="warning-btn" data-command="arrival">模拟到达终点</button>
            <button class="primary-btn" data-command="qr">二维码校验</button>
          </div>
        </div>
      </div>`,
    voice: `
      <div class="module-split">
        <div class="module-panel">
          <h3>🎙️ 浏览器录音</h3>
          <p>点击按钮对着麦克风说话，自动上传到腾讯云 ASR 识别并匹配命令。</p>
          <div class="voice-record-row">
            <button class="record-btn" id="voiceRecordBtn" data-command="voiceRecord">
              <span class="record-icon">🎤</span>
              <span class="record-label">点击开始录音</span>
            </button>
            <div class="record-status" id="voiceRecordStatus">
              <span>就绪，点击按钮开始说话</span>
            </div>
          </div>
          <div class="voice-result" id="voiceRecordResult">
            <span>等待录音...</span>
          </div>
        </div>
        <div class="module-panel">
          <h3>⌨️ 文本模拟</h3>
          <p>不想说话？直接打字模拟语音输入。</p>
          <div class="voice-input-row">
            <input type="text" id="voiceSimulateInput" class="voice-text-input"
                   placeholder="例如：今天天气怎么样 / 去A点 / 停下来 ..."
                   autocomplete="off">
            <button class="primary-btn" data-command="voiceSimulate">发送</button>
          </div>
          <div class="voice-result" id="voiceSimulateResult">
            <span>等待输入...</span>
          </div>
        </div>
      </div>
      <div class="voice-commands-panel">
        <h3>📋 可用语音命令</h3>
        <div class="voice-cmd-grid">
          ${['唤醒：你好小车', '开始送餐：去A点', '人脸识别', '循迹避障：出发', '天气：今天天气', '硬件自检', '语音测试', '停止：停下来', '退出'].map(c => `<span class="voice-cmd-chip">${c}</span>`).join('')}
        </div>
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>监听状态</span><strong id="voiceListeningText">${s.voice_listening ? '监听中' : '就绪'}</strong></section>
        <section class="summary-box"><span>最后命令</span><strong id="voiceCommandText">${s.voice_command || '无'}</strong></section>
        <section class="summary-box"><span>当前目标</span><strong id="voiceTargetText">${target ? `去 ${target} 点` : '未选择'}</strong></section>
        <section class="summary-box"><span>声光反馈</span><div id="voiceFeedbackText">${feedbackLinesHtml(s.rgb_color, s.buzzer)}</div></section>
      </div>`,
    feedback: `
      <div class="module-split">
        <div class="light-stage ${s.rgb_color}" id="lightStage"><i></i><div id="feedbackLiveText">${feedbackLinesHtml(s.rgb_color, s.buzzer)}</div></div>
        <div class="module-panel">
          <h3>状态对应关系</h3>
          <div class="feedback-matrix">
            <div class="feedback-state-card"><span>待机</span>${feedbackLinesHtml("blue", "off")}</div>
            <div class="feedback-state-card"><span>执行</span>${feedbackLinesHtml("yellow", "off")}</div>
            <div class="feedback-state-card"><span>遇障</span>${feedbackLinesHtml("red", "alarm")}</div>
            <div class="feedback-state-card"><span>完成</span>${feedbackLinesHtml("green", "beep")}</div>
          </div>
          <div class="action-row">
            <button class="success-btn" data-command="start">执行中反馈</button>
            <button class="warning-btn" data-command="obstacle">报警反馈</button>
            <button class="primary-btn" data-command="complete">完成反馈</button>
          </div>
        </div>
      </div>`,
    manual: `
      <div class="remote-panel">
        <div class="remote-grid">
          <button data-manual="turn_left">← 左转</button>
          <button data-manual="forward">↑ 前进</button>
          <button data-manual="turn_right">右转 →</button>
          <button data-manual="left">左移</button>
          <button data-manual="stop" class="stop-btn">停止</button>
          <button data-manual="right">右移</button>
          <span></span>
          <button data-manual="backward">↓ 后退</button>
          <span></span>
        </div>
        <div class="speed-control"><label for="speedRange">速度</label><input type="range" min="20" max="80" value="40" id="speedRange"><strong id="speedText">40</strong></div>
      </div>
      <div class="module-summary">
        <section class="summary-box"><span>控制权限</span><strong id="manualAuthText">${s.auth ? "已开放" : "未认证禁用"}</strong></section>
        <section class="summary-box"><span>当前模式</span><strong id="manualModeText">${modeLabel(s)}</strong></section>
        <section class="summary-box"><span>自动任务</span><strong id="manualRunText">${modeIsRunning(s) ? "执行中，仅允许停止" : "未执行，可手动控制"}</strong></section>
        <section class="summary-box"><span>速度值</span><strong id="manualSpeedText">40</strong></section>
      </div>`,
    logs: `
      <div class="log-toolbar">
        ${[
          ["all", "全部"], ["auth", "认证"], ["path", "路径"], ["run", "执行"], ["error", "异常"], ["done", "完成"],
        ].map(([key, label]) => `<button data-log-filter="${key}" class="${logFilter === key ? "active" : ""}">${label}</button>`).join("")}
        <button class="warning-btn" data-command="clearLogs">清空日志</button>
      </div>
      <ol class="full-log" id="fullLogs"></ol>
      <div class="module-summary">
        <section class="summary-box"><span>日志总数</span><strong id="logCountText">${s.logs.length}</strong></section>
        <section class="summary-box"><span>当前筛选</span><strong id="logFilterText">${logFilterLabel(logFilter)}</strong></section>
        <section class="summary-box"><span>连接状态</span><strong id="logBackendText">${backendOnline ? "后端在线" : "连接失败"}</strong></section>
        <section class="summary-box"><span>最近状态</span><strong id="logModeText">${modeLabel(s)}</strong></section>
      </div>`,
  };

  return modules[name] || modules.overview;
}

function visionCard(key, title, desc, command, buttonText) {
  return `<section class="vision-card">
    <div>
      <span>${title}</span>
      <strong id="${key}Result">${desc}</strong>
    </div>
    <p>时间：<b id="${key}Time">--:--:--</b></p>
    <em id="${key}State">待机</em>
    <button class="${command === "face" || command === "qr" ? "primary-btn" : "ghost-btn"}" data-command="${command}">${buttonText}</button>
  </section>`;
}

function renderWorkspace() {
  const s = safeStatus();
  const meta = moduleMeta[activeModule] || moduleMeta.overview;
  setText("moduleEyebrow", meta[0]);
  setText("moduleTitle", meta[1]);
  setText("moduleDesc", meta[2]);
  $("moduleStage").innerHTML = renderModuleBody(activeModule, s);
  bindDynamicControls();
}

function bindDynamicControls() {
  const stage = $("moduleStage");
  qsa("[data-target]", stage).forEach((button) => {
    button.addEventListener("click", () => {
      selectedTarget = button.dataset.target;
      updateAllViews(safeStatus());
    });
  });
  const taskType = $("taskType");
  if (taskType) {
    taskType.value = safeStatus().task_type || "delivery";
    taskType.addEventListener("change", () => updateAllViews(safeStatus()));
  }
  const speedRange = $("speedRange");
  if (speedRange) {
    speedRange.addEventListener("input", () => {
      setText("speedText", speedRange.value);
      setText("manualSpeedText", speedRange.value);
    });
  }
  qsa("[data-command]", stage).forEach((button) => {
    button.addEventListener("click", () => runCommand(button.dataset.command));
  });
  qsa("[data-manual]", stage).forEach((button) => {
    button.addEventListener("click", () => runManual(button.dataset.manual));
  });
  qsa("[data-open-module]", stage).forEach((button) => {
    button.addEventListener("click", () => enterConsole(button.dataset.openModule));
  });
  qsa("[data-log-filter]", stage).forEach((button) => {
    button.addEventListener("click", () => {
      logFilter = button.dataset.logFilter;
      qsa("[data-log-filter]", stage).forEach((item) => item.classList.toggle("active", item.dataset.logFilter === logFilter));
      updateLogs(safeStatus().logs);
    });
  });
  const voiceInput = $("voiceSimulateInput");
  if (voiceInput && !voiceInput._bound) {
    voiceInput._bound = true;
    voiceInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") runCommand("voiceSimulate");
    });
  }
  qsa(".voice-cmd-chip", stage).forEach((chip) => {
    chip.addEventListener("click", () => {
      const input = $("voiceSimulateInput");
      if (!input) return;
      const text = chip.textContent.includes("：")
        ? chip.textContent.split("：")[1]
        : chip.textContent;
      input.value = text;
      input.focus();
      runCommand("voiceSimulate");
    });
  });
  // 初始化浏览器录音器
  initVoiceRecorder();
}

function selectedBlockedNodes() {
  return qsa("[data-block-node]:checked", $("moduleStage")).map((input) => input.dataset.blockNode);
}

async function runCommand(command) {
  const s = safeStatus();
  const reason = commandBlockReason(command, s);
  if (reason) {
    alert(reason);
    return;
  }
  const taskType = $("taskType")?.value || s.task_type || "delivery";
  const target = currentTarget(s);
  const commands = {
    face: () => endpoint("/api/auth/face", {force_fail: false}),
    faceFail: () => endpoint("/api/auth/face", {force_fail: true}),
    logout: () => endpoint("/api/auth/logout"),
    plan: () => endpoint("/api/plan", {target, blocked: []}),
    replan: () => endpoint("/api/plan", {target, blocked: selectedBlockedNodes()}),
    start: () => endpoint("/api/task/start", {target, source: "web", task_type: taskType}),
    pause: () => endpoint("/api/task/pause"),
    resume: () => endpoint("/api/task/resume"),
    cancel: () => endpoint("/api/task/cancel"),
    obstacle: () => endpoint("/api/simulate/obstacle"),
    arrival: () => endpoint("/api/simulate/arrival"),
    qr: () => endpoint("/api/vision/qr_scan"),
    gesture: () => endpoint("/api/gesture/start"),
    voice: () => endpoint("/api/voice/start", {target}),
    voiceStart: () => endpoint("/api/voice/start", {target}),
    voiceStop: () => endpoint("/api/voice/stop"),
    voiceSimulate: async () => {
      const input = $("voiceSimulateInput");
      const text = input?.value?.trim();
      if (!text) {
        alert("请输入模拟语音文本");
        return {success: false};
      }
      const result = await endpoint("/api/voice/simulate", {text});
      const resultEl = $("voiceSimulateResult");
      if (resultEl) {
        if (result.command) {
          resultEl.innerHTML = `<span class="voice-match">✓ 匹配命令：<strong>${result.command}</strong> → action: ${result.action}</span>
            ${result.message ? `<p>${result.message}</p>` : ''}
            ${result.weather ? `<p class="voice-weather">${result.weather}</p>` : ''}`;
        } else {
          resultEl.innerHTML = `<span class="voice-no-match">✗ ${result.message || '未匹配到命令'}</span>`;
        }
      }
      input.value = '';
      input.focus();
      return result;
    },
    voiceRecord: async () => {
      // 通过全局 voiceRecorder 对象控制录音
      if (!window._voiceRecorder) return {success: false};
      if (window._voiceRecorder.state === "recording") {
        window._voiceRecorder.stop();
      } else {
        window._voiceRecorder.start();
      }
      return {success: true};
    },
    clearLogs: async () => {
      if (!confirm("确定清空任务日志吗？")) return {success: true, cancelled: true};
      return endpoint("/api/logs/clear");
    },
    // ---- 直连小车控制 ----
    carLineFollow: async () => {
      const speed = Number($("speedRange")?.value || 30);
      return endpoint("/api/car/line_follow", {target: currentTarget(s) || "B", speed});
    },
    carAvoid: async () => {
      const speed = Number($("speedRange")?.value || 30);
      return endpoint("/api/car/avoid", {speed});
    },
    carStop: () => endpoint("/api/car/stop"),
    carPause: () => endpoint("/api/car/pause"),
    carResume: () => endpoint("/api/car/resume"),
    complete: async () => {
      await endpoint("/api/simulate/arrival");
      return endpoint("/api/vision/qr_scan");
    },
  };
  const result = await commands[command]();
  if (result?.cancelled) return;
  if (result?.qr_value) {
    lastQrValue = result.qr_value;
    lastQrMatched = result.qr_value === (safeStatus().target || selectedTarget);
  }
  if (result?.success === false) alert(result.message || "操作失败");
  if (result?.status) updateStatus(result.status);
  else await refreshStatus();
}

async function runManual(action) {
  const reason = manualBlockReason(action, safeStatus());
  if (reason) {
    alert(reason);
    return;
  }
  const speed = Number($("speedRange")?.value || 40);
  const result = await endpoint("/api/control/manual", {
    action,
    speed: action === "stop" ? 0 : speed,
  });
  if (result.success === false) alert(result.message || "操作失败");
  if (result.status) updateStatus(result.status);
  else await refreshStatus();
}

function renderMaps(path = []) {
  const blocked = safeStatus().blocked || [];
  qsa("[data-map-svg]").forEach((svg) => {
    const nodes = svg.querySelector("[data-map-nodes]");
    const pathLine = svg.querySelector("[data-path-line]");
    if (!nodes || !pathLine) return;
    nodes.innerHTML = "";
    Object.entries(coords).forEach(([name, [x, y]]) => {
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.classList.add("node");
      if (path.includes(name)) group.classList.add("active-node");
      if (blocked.includes(name)) group.classList.add("blocked-node");
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", x);
      circle.setAttribute("cy", y);
      circle.setAttribute("r", ["A", "B", "C"].includes(name) ? 4.2 : 3.5);
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", x);
      text.setAttribute("y", y + 1.5);
      text.textContent = name;
      group.append(circle, text);
      nodes.append(group);
    });
    pathLine.setAttribute("points", path.map((name) => coords[name].join(",")).join(" "));
  });
}

function allLogs(logs = []) {
  return [...clientLogs, ...logs].sort((a, b) => (a.time || "").localeCompare(b.time || ""));
}

function logCategory(item) {
  const text = `${item.message || ""} ${item.level || ""}`;
  if (item.level === "danger" || text.includes("失败") || text.includes("异常") || text.includes("急停")) return "error";
  if (text.includes("认证") || text.includes("人脸")) return "auth";
  if (text.includes("路径") || text.includes("A*") || text.includes("目标点")) return "path";
  if (text.includes("巡线") || text.includes("任务") || text.includes("手动") || text.includes("继续")) return "run";
  if (text.includes("完成") || text.includes("校验成功")) return "done";
  return "all";
}

function filteredLogs(logs = []) {
  const data = allLogs(logs);
  if (logFilter === "all") return data;
  return data.filter((item) => logCategory(item) === logFilter);
}

function logLevelLabel(level) {
  const labels = {
    info: "信息",
    success: "成功",
    warning: "提醒",
    danger: "异常",
  };
  return labels[level] || "信息";
}

function logHtml(logs, emptyText = "暂无日志记录") {
  if (!logs.length) return `<li class="empty-state">${emptyText}</li>`;
  return logs.slice().reverse().map((item) => {
    const level = item.level || (logCategory(item) === "error" ? "danger" : "info");
    return `<li class="${level}"><time>[${item.time}]</time><span class="log-type ${level}">${logLevelLabel(level)}</span><span class="log-message">${item.message}</span></li>`;
  }).join("");
}

function updateLogs(logs = []) {
  const full = $("fullLogs");
  if (full) full.innerHTML = logHtml(filteredLogs(logs), "当前筛选条件下暂无日志");
}

function distanceLevel(distance) {
  const value = Number(distance);
  if (value < 15) return "danger";
  if (value <= 25) return "warning";
  return "safe";
}

function progressInfo(s) {
  if (!s.path.length) return {progress: 0, current: "S", next: "-"};
  if (modeIsDone(s)) return {progress: 100, current: s.path.at(-1), next: "-"};
  if (s.mode === "ARRIVAL_CHECK") return {progress: 85, current: s.path.at(-1), next: "二维码校验"};
  if (modeIsRunning(s)) return {progress: 55, current: s.path[1] || s.path[0], next: s.path[2] || s.path.at(-1)};
  if (modeIsObstacle(s)) return {progress: 48, current: s.path[1] || s.path[0], next: "等待避障"};
  if (s.mode === "READY") return {progress: 20, current: "S", next: s.path[1] || s.path.at(-1)};
  return {progress: s.path.length ? 15 : 0, current: "S", next: s.path[1] || "-"};
}

function updateOverview(s) {
  const target = currentTarget(s);
  ["auth", "task", "path", "run", "safety", "vision", "arrival", "feedback"].forEach((key) => {
    const [label, state] = stepState(key, s);
    const card = document.querySelector(`[data-step-card="${key}"]`);
    const status = document.querySelector(`[data-step-status="${key}"]`);
    if (card) card.className = `process-card status-${state}`;
    if (status) status.textContent = label;
  });
  setText("nextSuggestion", nextSuggestion(s));
  setText("overviewModeText", backendOnline ? modeLabel(s) : "后端连接失败");
  setText("overviewTargetText", targetLabel(target));
  setText("overviewPathText", pathText(s.path));
  setText("overviewSafetyText", backendOnline ? s.strategy : "小车离线");
}

function updateModuleFields(s) {
  const target = currentTarget(s);
  qsa("[data-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === target);
  });
  const taskType = $("taskType")?.value || s.task_type || "delivery";

  updateOverview(s);
  setText("authStateText", s.auth ? "已认证" : "未认证");
  setText("authUserText", s.user);
  setText("authVisionText", s.vision_result);
  setText("authCameraResult", s.vision_result);
  setHTML("authFeedbackText", feedbackLinesHtml(s.rgb_color, s.buzzer));

  setText("taskTypeText", taskTypeLabel[taskType] || "未知任务类型");
  setText("taskTargetText", targetLabel(target));
  setText("taskStatusText", statusLabel(s.task_status));
  const taskBlocked = s.task_status === "error_stop" || modeIsObstacle(s);
  const taskAlert = $("taskAlert");
  if (taskAlert) taskAlert.hidden = !taskBlocked;
  const taskActionRow = $("taskActionRow");
  if (taskActionRow && taskActionRow.dataset.blocked !== String(taskBlocked)) {
    taskActionRow.dataset.blocked = String(taskBlocked);
    taskActionRow.innerHTML = taskActionButtons(s);
    taskActionRow.querySelectorAll("[data-command]").forEach((button) => {
      button.addEventListener("click", () => runCommand(button.dataset.command));
    });
  }

  const route = pathText(s.path);
  setText("pathRouteText", route);
  setText("pathTargetText", target || "未选择");
  setText("blockedText", s.blocked.length ? s.blocked.join("、") : "无");
  setText("pathCostText", s.cost ?? "-");

  const progress = progressInfo(s);
  setText("runModeText", modeLabel(s));
  setText("runPathText", route);
  setText("currentNodeText", progress.current);
  setText("nextNodeText", progress.next);
  setText("progressText", `${progress.progress}%`);
  setText("runLineBitsText", s.line_bits.join(" "));
  setText("runStageText", modeIsRunning(s) ? "执行中" : modeLabel(s));
  const progressBar = $("taskProgressBar");
  if (progressBar) progressBar.style.width = `${progress.progress}%`;
  $("runStage")?.classList.toggle("running", modeIsRunning(s));

  const level = distanceLevel(s.distance_cm);
  setText("safetyDistanceText", s.distance_cm);
  const safety = $("safetyReadout");
  if (safety) safety.className = `safety-readout ${level}`;
  setText("safetyObstacleText", s.obstacle ? "检测到障碍物" : "安全");
  setText("safetyLineBitsText", s.line_bits.join(" "));
  setText("safetyLineStateText", lineStateLabel(s.line_state));
  setText("safetyStrategyText", s.strategy);
  setText("obstacleNote", modeIsObstacle(s) ? "已停车，等待障碍移除。" : "当前距离处于安全范围。");

  const now = new Date().toLocaleTimeString("zh-CN", {hour12: false});
  setText("faceResult", s.auth ? `已认证：${s.user}` : s.vision_result);
  setText("qrResult", lastQrValue === "-" ? "等待二维码" : `识别到 ${lastQrValue}`);
  setText("gestureResult", s.mode === "GESTURE_MODE" ? s.vision_result : "等待手势");
  setText("voiceResult", s.voice_command || (target ? `语音：去 ${target} 点` : "语音：请选择目标"));
  setText("visionModeText", s.vision_mode);
  setText("visionResultText", s.vision_result);
  setText("voiceTargetText", target ? `去 ${target} 点` : "未选择目标");
  setHTML("visionFeedbackText", feedbackLinesHtml(s.rgb_color, s.buzzer));
  ["face", "qr", "gesture", "voice"].forEach((key) => {
    setText(`${key}Time`, now);
    setText(`${key}State`, key === "face" && s.auth ? "完成" : "待机");
  });
  const voiceButton = document.querySelector('[data-command="voice"]');
  if (voiceButton) voiceButton.textContent = target ? `语音：去 ${target} 点` : "语音：请选择目标";

  setText("arrivalTargetBig", target ? `${target} 点` : "未选择");
  setText("arrivalTargetText", targetLabel(target));
  setText("qrValueText", lastQrValue);
  setText("qrMatchText", lastQrMatched === true ? "一致" : lastQrMatched === false ? "不一致" : "待校验");
  setText("arrivalStatusText", statusLabel(s.task_status));

  const lightStage = $("lightStage");
  if (lightStage) lightStage.className = `light-stage ${s.rgb_color}`;
  setHTML("feedbackLiveText", feedbackLinesHtml(s.rgb_color, s.buzzer));

  setText("voiceListeningText", s.voice_listening ? "监听中" : "未启动");
  setText("voiceCommandText", s.voice_command || "无");
  setText("voiceTargetText", target ? `去 ${target} 点` : "未选择");
  setHTML("voiceFeedbackText", feedbackLinesHtml(s.rgb_color, s.buzzer));
  const listenResult = $("voiceListenResult");
  if (listenResult) listenResult.innerHTML = `<span>监听状态：${s.voice_listening ? '🔊 监听中...' : '⏸️ 未启动'}</span>`;

  setText("manualAuthText", s.auth ? "已开放" : "未认证禁用");
  setText("manualModeText", modeLabel(s));
  setText("manualRunText", modeIsRunning(s) ? "执行中，仅允许停止" : "未执行，可手动控制");
  setText("manualSpeedText", $("speedRange")?.value || "40");

  setText("logCountText", allLogs(s.logs).length);
  setText("logFilterText", logFilterLabel(logFilter));
  setText("logBackendText", backendOnline ? "后端在线" : "连接失败");
  setText("logModeText", modeLabel(s));
}

function updateTopAndLive(s) {
  const target = currentTarget(s);
  setChip($("topModeText"), backendOnline ? modeLabel(s) : "离线", s.mode === "ERROR_STOP" || modeIsObstacle(s) || !backendOnline ? "danger" : "strong");
  setChip($("userBadge"), s.auth ? "已认证" : "未认证", s.auth ? "success" : "danger");
  setChip($("carBadge"), backendOnline && s.car_connected ? "在线" : "离线", backendOnline && s.car_connected ? "success" : "danger");
  setChip($("targetBadge"), target ? `目标：${target}` : "目标：无");

  setText("visionMode", s.vision_mode);
  setText("visionResult", backendOnline ? s.vision_result : "后端连接失败");
  setText("pathStatus", s.path_status);
  setText("routeText", pathText(s.path));
  setText("strategy", s.strategy);
  setHTML("distance", `<span class="distance-number">${s.distance_cm}</span><span class="distance-unit">cm</span>`);
  const safetyLevel = distanceLevel(s.distance_cm);
  const safetyCard = document.querySelector(".safety-card");
  if (safetyCard) {
    safetyCard.classList.toggle("is-safe", safetyLevel === "safe" && !s.obstacle);
    safetyCard.classList.toggle("is-warning", safetyLevel === "warning" && !s.obstacle);
    safetyCard.classList.toggle("is-danger", safetyLevel === "danger" || s.obstacle);
  }
  const bar = $("distanceBar");
  if (bar) {
    bar.style.width = `${Math.max(8, Math.min(100, Number(s.distance_cm) * 2 || 8))}%`;
    bar.className = safetyLevel;
  }
  setText("obstacle", s.obstacle ? "检测到障碍物" : "安全");
  $("obstacle")?.classList.toggle("danger-text", s.obstacle);
  $("obstacle")?.classList.toggle("success-text", !s.obstacle);
  setText("lineBits", s.line_bits.join(" "));
  setText("lineState", lineStateLabel(s.line_state));
  setHTML("feedbackState", miniFeedbackHtml(s.rgb_color, s.buzzer));
}

function updateAllViews(s) {
  updateTopAndLive(s);
  renderMaps(s.path);
  updateLogs(s.logs);
  updateModuleFields(s);
  applyControlState(s);
}

function updateStatus(status) {
  backendOnline = true;
  if (status.target) selectedTarget = status.target;
  if (status.auth === false || status.mode === "AUTH_REQUIRED") selectedTarget = null;
  statusCache = {...safeStatus(), ...status};
  updateAllViews(safeStatus());
}

function handleOffline(message) {
  backendOnline = false;
  addClientLog(message, "danger");
  const s = {...safeStatus(), car_connected: false, mode_text: "后端连接失败"};
  updateAllViews(s);
}

// ======================== 浏览器录音器（PCM WAV 直出） ========================
function initVoiceRecorder() {
  const btn = $("voiceRecordBtn");
  const statusEl = $("voiceRecordStatus");
  const resultEl = $("voiceRecordResult");
  if (!btn || btn._recorderInited) return;
  btn._recorderInited = true;

  let audioCtx = null;
  let stream = null;
  let scriptNode = null;
  let pcmChunks = [];
  const SAMPLE_RATE = 16000;

  function buildWav(pcmData) {
    // PCM Int16 → WAV 文件
    const numSamples = pcmData.byteLength / 2;
    const buf = new ArrayBuffer(44 + pcmData.byteLength);
    const view = new DataView(buf);
    // RIFF header
    writeStr(view, 0, "RIFF");
    view.setUint32(4, 36 + pcmData.byteLength, true);
    writeStr(view, 8, "WAVE");
    // fmt chunk
    writeStr(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);        // PCM
    view.setUint16(22, 1, true);        // mono
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);         // block align
    view.setUint16(34, 16, true);        // bits per sample
    // data chunk
    writeStr(view, 36, "data");
    view.setUint32(40, pcmData.byteLength, true);
    new Uint8Array(buf, 44).set(new Uint8Array(pcmData));
    return new Blob([buf], {type: "audio/wav"});
  }

  function writeStr(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  window._voiceRecorder = {
    state: "idle",
    start: async () => {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("当前浏览器不支持麦克风录音，请使用 Chrome 或 Edge");
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true}
        });
        audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: SAMPLE_RATE});
        const source = audioCtx.createMediaStreamSource(stream);

        // 降采样到 16kHz 的 ScriptProcessor
        scriptNode = audioCtx.createScriptProcessor(4096, 1, 1);
        pcmChunks = [];

        scriptNode.onaudioprocess = (e) => {
          const input = e.inputBuffer.getChannelData(0);
          // Float32 → Int16 PCM
          const pcm = new Int16Array(input.length);
          for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          pcmChunks.push(pcm.buffer);
        };

        source.connect(scriptNode);
        scriptNode.connect(audioCtx.destination);

        window._voiceRecorder.state = "recording";
        btn.classList.add("recording");
        btn.querySelector(".record-label").textContent = "录音中...点击停止";
        statusEl.innerHTML = "<span style=\"color:#c9922e\">🔴 正在录音，请说话...</span>";

        // 5 秒后自动停止
        setTimeout(() => {
          if (window._voiceRecorder.state === "recording") {
            window._voiceRecorder.stop();
          }
        }, 5000);

      } catch (err) {
        if (err.name === "NotAllowedError") {
          alert("麦克风权限被拒绝，请在浏览器设置中允许访问麦克风");
        } else {
          alert(`麦克风启动失败：${err.message}`);
        }
        window._voiceRecorder.state = "idle";
      }
    },
    stop: () => {
      if (window._voiceRecorder.state !== "recording") return;
      window._voiceRecorder.state = "processing";

      // 断开音频流
      if (scriptNode) {
        scriptNode.disconnect();
        scriptNode = null;
      }
      if (audioCtx) {
        audioCtx.close().catch(() => {});
        audioCtx = null;
      }
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
        stream = null;
      }

      btn.classList.remove("recording");
      btn.classList.add("processing");
      btn.querySelector(".record-label").textContent = "识别中...";
      statusEl.innerHTML = "<span>识别中...</span>";

      // 合并 PCM 数据并构建 WAV
      const totalLen = pcmChunks.reduce((sum, b) => sum + b.byteLength, 0);
      const combined = new Uint8Array(totalLen);
      let offset = 0;
      for (const buf of pcmChunks) {
        combined.set(new Uint8Array(buf), offset);
        offset += buf.byteLength;
      }

      if (combined.length < 1000) {
        btn.classList.remove("processing");
        window._voiceRecorder.state = "idle";
        btn.querySelector(".record-label").textContent = "点击开始录音";
        statusEl.innerHTML = "<span class=\"voice-no-match\">✗ 录音太短，请重试</span>";
        return;
      }

      const wavBlob = buildWav(combined.buffer.slice(0, combined.byteLength));

      // 上传 WAV 到后端 ASR
      const formData = new FormData();
      formData.append("audio", wavBlob, "recording.wav");

      fetch("/api/voice/recognize", {method: "POST", body: formData})
        .then((res) => res.json())
        .then((result) => {
          btn.classList.remove("processing");
          window._voiceRecorder.state = "idle";
          btn.querySelector(".record-label").textContent = "点击开始录音";
          btn.classList.remove("recording");

          if (result.command) {
            statusEl.innerHTML = `<span class="voice-match">✓ 识别成功：<strong>${result.text}</strong> → ${result.command}</span>`;
            if (resultEl) {
              resultEl.innerHTML = `<span class="voice-match">✓ ${result.message || ""}</span>
                ${result.weather ? `<p class="voice-weather">${result.weather}</p>` : ""}`;
            }
          } else if (result.text) {
            statusEl.innerHTML = `<span class="voice-no-match">识别文本：「${result.text}」→ ${result.message || "未匹配"}</span>`;
          } else {
            statusEl.innerHTML = `<span class="voice-no-match">✗ ${result.message || "未识别到语音"}</span>`;
          }

          if (result.status) updateStatus(result.status);
          else refreshStatus();
        })
        .catch((err) => {
          btn.classList.remove("processing", "recording");
          window._voiceRecorder.state = "idle";
          btn.querySelector(".record-label").textContent = "点击开始录音";
          statusEl.innerHTML = `<span class="voice-no-match">✗ 上传失败：${err.message}</span>`;
        });
    },
  };
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status", {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const status = await res.json();
    updateStatus(status);
  } catch (error) {
    handleOffline(networkErrorMessage(error));
  }
}

qsa("[data-open-module]").forEach((button) => {
  button.addEventListener("click", () => enterConsole(button.dataset.openModule));
});

qsa(".module-tabs button").forEach((button) => {
  button.addEventListener("click", () => setActiveModule(button.dataset.module));
});

$("emergencyStop").addEventListener("click", async () => {
  if (!backendOnline) {
    alert("后端连接失败，急停接口不可用");
    return;
  }
  await endpoint("/api/control/emergency_stop");
  await refreshStatus();
});

const clearLogsButton = $("clearLogs");
if (clearLogsButton) {
  clearLogsButton.addEventListener("click", async () => {
    if (!confirm("确定清空任务日志吗？")) return;
    const result = await endpoint("/api/logs/clear");
    if (result.logs) updateLogs(result.logs);
    await refreshStatus();
  });
}

statusCache = {...defaultStatus};
renderWorkspace();
updateAllViews(safeStatus());
refreshStatus();
setInterval(refreshStatus, 2500);
