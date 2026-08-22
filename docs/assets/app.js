"use strict";

const CONFIG = window.ECE329_CONFIG || { API_BASE_URL: "", REQUEST_TIMEOUT_MS: 70000 };
const STORAGE_KEY = "ece329-lab-studio-session-v1";
const DESIGN_TOKEN_KEY = "ece329-design-access-token-v1";
const ACCESS_CODE_KEY = "ece329-course-access-code-v1";
const LEGACY_INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们先从讲义中的概念出发探索想法，不急着写完整方案。\n\n请描述一个你感兴趣的电磁现象，或者告诉我你还没有具体方向。";
const PREVIOUS_INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们会从ECE329课上所学的电磁场、电磁波和传输线概念出发，一起探索实验想法，不急着写完整方案。\n\n你可以描述一个感兴趣的现象，例如静电场与材料边界、磁场与电磁感应、电磁波的偏振与反射，或传输线中的反射与驻波。如果暂时没有方向，也可以直接告诉我。";
const INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们先了解你的想法，再一起把它发展成清晰的实验方向，不急着写完整方案。\n\n请先用自己的话说说：你目前对哪个ECE329现象或概念有兴趣？如果还没有具体思路，也可以直接告诉我，我会先带你浏览课上所学内容的大致方向。";

class ApiError extends Error {
  constructor(message, status = 0, code = "request_failed") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const STAGES = [
  ["IDEA_BRAINSTORMING", "实验想法探索与完善"],
  ["COURSE_MAPPING_AND_DIRECTION", "课程映射与实验方向"],
  ["LEARNING_OBJECTIVES", "学习目标"],
  ["RESEARCH_QUESTION", "研究问题"],
  ["THEORETICAL_FRAMEWORK", "理论框架"],
  ["HYPOTHESIS", "假设与预期趋势"],
  ["CONCEPTUAL_OR_VR_SETUP", "概念实验结构"],
  ["VARIABLES_AND_CONDITIONS", "变量与条件"],
  ["CONCEPTUAL_PROCEDURE", "概念实验流程"],
  ["EXPECTED_DATA_VISUALIZATION", "预期数据可视化"],
  ["RESULT_INTERPRETATION", "可能结果及解释"],
  ["DESIGN_VALUE_AND_LIMITATIONS", "设计价值与局限"],
  ["STUDENT_SYNTHESIS_OR_EMVR_OUTPUT", "学生总结"],
];

const EMVR_STAGE_TITLES = Object.freeze({
  CONCEPTUAL_OR_VR_SETUP: "Unity VR模拟实验设计",
  STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "EMVR方案汇总",
});

const DEMO_KNOWLEDGE = [
  {
    keywords: ["驻波", "传输线", "负载", "阻抗", "反射"],
    conceptId: "transmission_line_topic",
    formulaId: "load_reflection_phasor",
    lecture: "Lectures 27–39",
    pages: "238–324",
    title: "Transmission-line systems",
    concepts: "导波传播、反射与暂态、驻波与共振、阻抗匹配、有损线路",
    formula: "Γ_L=(Z_L-Z₀)/(Z_L+Z₀); Γ(d)=Γ_L e^{-j2βd}",
    formulaPages: "289–290",
    options: ["传输线与反射、暂态响应的关系", "传输线与驻波、共振模式的关系", "传输线与阻抗匹配、功率反射的关系"],
  },
  {
    keywords: ["偏振", "圆偏振", "线偏振"],
    conceptId: "lecture_24",
    formulaId: "circular_polarization",
    lecture: "Lecture 24",
    pages: "217–225",
    title: "Signal transmission and circular polarization",
    concepts: "线偏振、圆偏振、正交分量和相位差",
    formula: "E=m(t-z/v)[x̂ cos(ωt-βz) ± ŷ sin(ωt-βz)]",
    formulaPages: "218–220",
    options: ["时变电场、磁场与波传播的关系", "正交场分量与偏振轨迹的关系", "场幅度、相位与功率流的关系"],
  },
  {
    keywords: ["屏蔽", "趋肤", "导体", "穿透", "衰减"],
    conceptId: "lecture_23",
    formulaId: "skin_depth",
    lecture: "Lecture 23",
    pages: "211–216",
    title: "Imperfect dielectrics and good conductors",
    concepts: "良导体近似、趋肤深度、穿透深度、功率衰减",
    formula: "δ = 1/α; for a good conductor δ≈1/√(πfμσ)",
    formulaPages: "210–211, 216",
    options: ["界面材料与反射、透射的关系", "频率、导电性质与穿透/衰减的关系", "场的传播路径与屏蔽、串扰的关系"],
  },
  {
    keywords: ["电容", "极板", "同轴", "介电"],
    conceptId: "lecture_10",
    formulaId: "capacitance_definition",
    lecture: "Lecture 10",
    pages: "96–104",
    title: "Capacitance and conductance",
    concepts: "电容、电导、平行板与同轴结构、电场储能",
    formula: "Q = CV",
    formulaPages: "96–97",
    options: ["边界形状与电势/电场分布的关系", "介质分布与电场、位移场的关系", "电容结构与场能量的关系"],
  },
  {
    keywords: ["感应", "法拉第", "磁通", "电动势"],
    conceptId: "lecture_14",
    formulaId: "faraday_generalized_emf",
    lecture: "Lecture 14",
    pages: "130–142",
    title: "Faraday's law and induced emf",
    concepts: "Faraday定律、感应电动势、运动电动势、磁通",
    formula: "ℰ = ∮_C (E+v×B)·dl = -dΦ_B/dt",
    formulaPages: "131–132",
    options: ["电流几何与磁场空间结构的关系", "磁通变化、运动与感应电动势的关系", "场能量与电磁力、传感或驱动的关系"],
  },
];

const FALLBACK_EVIDENCE = {
  lecture: "Course overview",
  pages: "10–12",
  title: "ECE329 course blocks",
  concepts: "Electrostatics；Magnetism；Electromagnetics, waves and transmission lines",
  formula: null,
  formulaPages: null,
  options: [
    "静电场与电势、介质、极化或电容结构之间的关系",
    "磁场与电流、磁通变化、电磁感应或电感之间的关系",
    "电磁波与偏振、界面反射、导体衰减或传输线之间的关系",
  ],
};

const DEMO_STAGE_PROMPTS = [
  "你更想探索这个主题与哪一类现象或概念之间的关系？可以参考这些例子，也可以提出自己的关联。",
  "你希望把哪一个ECE329课程概念作为实验的主要理论核心？",
  "你最希望学生通过这个实验获得哪一种能力？",
  "你希望实验主要改变哪一个因素？",
  "哪一个ECE329理论关系式最直接连接自变量和观察量？",
  "当主要自变量增大时，你预计因变量怎样变化？",
  "实验中由什么对象或条件产生目标电磁场？",
  "你准备主动改变的一个量是什么？",
  "改变主要变量前，需要建立什么基准状态？",
  "你预计理论曲线最可能呈现什么形状？",
  "如果结果没有明显变化，最值得先检查哪个理论假设？",
  "这个设计依赖的哪个理想化假设最可能限制结论？",
  "请先用两到三句话总结实验想研究什么，以及为什么值得研究。",
];

const dom = {
  connectionBadge: document.querySelector("#connectionBadge"),
  offlineNotice: document.querySelector("#offlineNotice"),
  resetButton: document.querySelector("#resetButton"),
  stageList: document.querySelector("#stageList"),
  stageCounter: document.querySelector("#stageCounter"),
  progressPercent: document.querySelector("#progressPercent"),
  progressBar: document.querySelector("#progressBar"),
  currentStageTitle: document.querySelector("#currentStageTitle"),
  modeLabel: document.querySelector("#modeLabel"),
  modeCode: document.querySelector("#modeCode"),
  messageList: document.querySelector("#messageList"),
  quickActions: document.querySelector("#quickActions"),
  chatForm: document.querySelector("#chatForm"),
  chatInput: document.querySelector("#chatInput"),
  sendButton: document.querySelector("#sendButton"),
  evidenceContent: document.querySelector("#evidenceContent"),
  designNotes: document.querySelector("#designNotes"),
  chart: document.querySelector("#theoryChart"),
  chartParameter: document.querySelector("#chartParameter"),
  parameterValue: document.querySelector("#parameterValue"),
  chartLegendLabel: document.querySelector("#chartLegendLabel"),
  chartDescription: document.querySelector("#chartDescription"),
  toast: document.querySelector("#toast"),
};

let state = loadState();
let typingMessageId = null;
let toastTimer = null;
let connectionState = apiBase() ? "checking" : "demo";

function initialState() {
  return {
    designId: null,
    sessionKind: null,
    stageIndex: 0,
    mode: "GUIDED_DESIGN",
    messages: [
      {
        id: crypto.randomUUID(),
        role: "assistant",
        text: INITIAL_GREETING,
        meta: "ECE329 Design Guide",
        tags: ["阶段 1", "ECE329课程相关"],
      },
    ],
    evidence: null,
    visualization: null,
    quickActions: [],
    notes: [],
    pendingOptionId: null,
    pendingDirection: null,
    stageOnePhase: "BREADTH_EXPLORATION",
    pendingSummary: null,
    summarySections: [],
    lastStudentInput: null,
  };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (saved && Array.isArray(saved.messages) && Number.isInteger(saved.stageIndex)) {
      saved.messages = saved.messages.map((message) => (
        message.role === "assistant"
          && [LEGACY_INITIAL_GREETING, PREVIOUS_INITIAL_GREETING].includes(message.text)
          ? { ...message, text: INITIAL_GREETING, tags: ["阶段 1", "ECE329课程相关"] }
          : message
      ));
      if (!Array.isArray(saved.summarySections)) saved.summarySections = [];
      return { ...initialState(), ...saved };
    }
  } catch (error) {
    console.warn("Unable to restore local session", error);
  }
  return initialState();
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function apiBase() {
  return String(CONFIG.API_BASE_URL || "").trim().replace(/\/$/, "");
}

async function apiRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    Number(CONFIG.REQUEST_TIMEOUT_MS) || 70000,
  );
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(
        payload.detail || payload.error || `HTTP ${response.status}`,
        response.status,
        payload.error || "request_failed",
      );
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function checkConnection() {
  if (!apiBase()) {
    setConnectionState("demo", "本地示例 · 课程服务未连接");
    dom.offlineNotice.hidden = false;
    return;
  }
  try {
    await apiRequest("/ready", { method: "GET" });
    setConnectionState("online", "课程服务已连接");
    dom.offlineNotice.hidden = true;
  } catch (error) {
    setConnectionState("error", "课程服务连接失败");
    dom.offlineNotice.hidden = false;
    dom.offlineNotice.querySelector("strong").textContent = "离线模式";
    dom.offlineNotice.querySelector("span").textContent = "无法连接课程服务，暂时使用本地示例回答。";
  }
}

function setConnectionState(kind, label) {
  connectionState = kind;
  dom.connectionBadge.className = `connection-badge ${kind}`;
  dom.connectionBadge.querySelector("span:last-child").textContent = label;
}

function render() {
  renderStages();
  renderMessages();
  renderQuickActions();
  renderEvidence();
  renderNotes();
  renderMode();
  drawChart();
}

function renderStages() {
  dom.stageList.replaceChildren();
  STAGES.forEach(([id], index) => {
    const title = stageTitle(index);
    const item = document.createElement("li");
    item.className = "stage-item";
    item.dataset.stageId = id;
    if (index < state.stageIndex) item.classList.add("complete");
    if (index === state.stageIndex) item.classList.add("current");
    item.setAttribute("aria-current", index === state.stageIndex ? "step" : "false");

    const number = document.createElement("span");
    number.className = "stage-number";
    number.textContent = index < state.stageIndex ? "✓" : String(index + 1);

    const label = document.createElement("span");
    label.className = "stage-label";
    label.textContent = title;

    item.append(number, label);
    dom.stageList.append(item);
  });

  const progress = Math.round((state.stageIndex / (STAGES.length - 1)) * 100);
  dom.stageCounter.textContent = `阶段 ${state.stageIndex + 1} / ${STAGES.length}`;
  dom.progressPercent.textContent = `${progress}%`;
  dom.progressBar.style.width = `${progress}%`;
  dom.currentStageTitle.textContent = stageTitle(state.stageIndex);
}

function stageTitle(index) {
  const [id, guidedTitle] = STAGES[index];
  if (state.mode === "EMVR_DIRECT" && EMVR_STAGE_TITLES[id]) {
    return EMVR_STAGE_TITLES[id];
  }
  return guidedTitle;
}

function renderMessages() {
  dom.messageList.replaceChildren();
  state.messages.forEach((message) => dom.messageList.append(createMessageElement(message)));
  dom.messageList.scrollTop = dom.messageList.scrollHeight;
}

function createMessageElement(message) {
  const article = document.createElement("article");
  article.className = `message ${message.role}`;
  article.dataset.messageId = message.id;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = message.role === "user" ? "YOU" : "329";

  const content = document.createElement("div");
  content.className = "message-content";

  const meta = document.createElement("p");
  meta.className = "message-meta";
  meta.textContent = message.meta || (message.role === "user" ? "你" : "ECE329 Design Guide");

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  if (message.typing) {
    bubble.classList.add("typing-bubble");
    bubble.setAttribute("aria-label", "正在生成回答");
    bubble.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
  } else {
    bubble.textContent = message.text;
  }

  content.append(meta, bubble);
  if (message.tags?.length) {
    const tags = document.createElement("div");
    tags.className = "message-tags";
    message.tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "message-tag";
      chip.textContent = tag;
      tags.append(chip);
    });
    content.append(tags);
  }

  article.append(avatar, content);
  return article;
}

function renderQuickActions() {
  dom.quickActions.replaceChildren();
  (state.quickActions || []).forEach((action) => {
    const { label, optionId } = normalizeQuickAction(action);
    if (!label) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-action";
    button.textContent = label;
    button.addEventListener("click", () => {
      dom.chatInput.value = label;
      state.pendingOptionId = optionId;
      autoGrowInput();
      dom.chatInput.focus();
    });
    dom.quickActions.append(button);
  });
}

function normalizeQuickAction(action) {
  if (typeof action === "string") return { label: action, optionId: null };
  if (!action || typeof action !== "object") return { label: "", optionId: null };
  return {
    label: String(action.label || action.focus || action.direction || "").trim(),
    optionId: typeof action.option_id === "string" ? action.option_id : null,
  };
}

function renderEvidence() {
  dom.evidenceContent.replaceChildren();
  if (!state.evidence) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "输入想法后，这里会显示匹配的讲次、概念和公式页码。";
    dom.evidenceContent.append(empty);
    return;
  }

  const evidence = Array.isArray(state.evidence) ? state.evidence : [state.evidence];
  evidence.slice(0, 4).forEach((item) => {
    const card = document.createElement("div");
    card.className = "evidence-item";

    const kicker = document.createElement("div");
    kicker.className = "evidence-kicker";
    const lecture = document.createElement("span");
    lecture.textContent = item.lecture || item.title || "Lecture reference";
    const pages = document.createElement("span");
    pages.textContent = `PDF ${formatPages(item.pages)}`;
    kicker.append(lecture, pages);

    const title = document.createElement("strong");
    title.textContent = item.title || item.name || "ECE329 concept";
    card.append(kicker, title);

    const detail = document.createElement("p");
    detail.textContent = formatConcepts(item.concepts) || item.expression || "来源已由lecture note知识目录固定。";
    card.append(detail);

    if (item.formula || item.expression) {
      const formula = document.createElement("p");
      const formulaPages = item.formulaPages || formatPages(item.pages);
      formula.textContent = `${item.formula || item.expression} · PDF ${formulaPages}`;
      card.append(formula);
    }
    dom.evidenceContent.append(card);
  });
}

function formatPages(pages) {
  if (Array.isArray(pages)) return pages.join("–");
  return String(pages || "页码待返回");
}

function formatConcepts(concepts) {
  if (Array.isArray(concepts)) return concepts.join("、");
  return concepts ? String(concepts) : "";
}

function renderNotes() {
  dom.designNotes.replaceChildren();
  const notes = state.notes?.length ? state.notes : ["尚未记录实验想法"];
  notes.slice(-5).forEach((note) => {
    const item = document.createElement("li");
    item.textContent = note;
    dom.designNotes.append(item);
  });
}

function renderMode() {
  const emvr = state.mode === "EMVR_DIRECT";
  dom.modeLabel.textContent = emvr ? "EMVR模式" : "引导模式";
  dom.modeCode.textContent = emvr ? "EMVR DIRECT" : "GUIDED";
}

function addMessage(role, text, tags = [], options = {}) {
  const message = {
    id: crypto.randomUUID(),
    role,
    text,
    tags,
    meta: options.meta,
    typing: options.typing || false,
  };
  state.messages.push(message);
  saveState();
  renderMessages();
  return message.id;
}

function showTyping() {
  typingMessageId = addMessage("assistant", "", [], { typing: true });
}

function hideTyping() {
  state.messages = state.messages.filter((item) => item.id !== typingMessageId);
  typingMessageId = null;
}

async function handleSubmit(event) {
  event.preventDefault();
  const message = dom.chatInput.value.trim();
  if (!message || dom.sendButton.disabled) return;

  if (state.stageIndex === 0 && !isAdvanceIntent(message) && !state.pendingDirection) {
    state.pendingDirection = message;
  }
  const isSummaryContribution = (
    state.stageIndex === STAGES.length - 1 && !isAdvanceIntent(message)
  );
  state.lastStudentInput = message;
  addMessage("user", message);
  dom.chatInput.value = "";
  autoGrowInput();
  setBusy(true);
  showTyping();

  try {
    let response;
    if (apiBase() && connectionState !== "online") {
      await checkConnection();
    }
    if (apiBase() && connectionState === "online") {
      response = await sendToApi(message);
      response._runtime_source = "api";
    } else if (!apiBase()) {
      await wait(420);
      response = createDemoResponse(message);
    } else {
      throw new ApiError("Backend is not ready", 0, "backend_unavailable");
    }
    if (
      isSummaryContribution
      && response.request_rejected !== true
      && response.stage_payload?.request_rejected !== true
    ) {
      if (!state.summarySections.includes(message)) state.summarySections.push(message);
      state.pendingSummary = state.summarySections.join("\n\n");
    }
    hideTyping();
    applyResponse(response, message);
  } catch (error) {
    hideTyping();
    if (error instanceof ApiError && ["session_not_found", "access_denied"].includes(error.code)) {
      const hadDesign = Boolean(state.designId);
      clearApiSession();
      setConnectionState("online", "课程服务已连接");
      const guidance = hadDesign
        ? "之前的设计记录已经失效。请重新输入实验想法，开始一次新的设计。"
        : "课程访问码不正确或尚未提供。请再次发送，并输入教师或课程管理员提供的访问码。";
      addMessage("assistant", guidance, ["需要重新连接"], { meta: "ECE329 Agent" });
      state.quickActions = ["传输线驻波", "电磁波偏振", "导体中的衰减"];
      showToast("未切换为本地示例，请按提示重新连接");
      return;
    }
    if (error instanceof ApiError && error.status === 429) {
      setConnectionState("online", "课程服务已连接");
      addMessage(
        "assistant",
        "请求过于频繁，请等待一会儿后重新发送。当前设计和进度已经保留。",
        ["稍后重试"],
        { meta: "ECE329 Agent" },
      );
      state.quickActions = [message];
      return;
    }
    if (error instanceof ApiError && error.status === 409) {
      await reloadApiDesignState();
      addMessage(
        "assistant",
        "设计可能已在另一个窗口更新。我已同步当前设计，请重新发送本轮内容。",
        ["状态已刷新"],
        { meta: "ECE329 Agent" },
      );
      state.quickActions = [message];
      return;
    }
    setConnectionState("error", "课程服务暂时不可用");
    dom.offlineNotice.hidden = false;
    dom.offlineNotice.querySelector("strong").textContent = "连接暂时中断";
    dom.offlineNotice.querySelector("span").textContent = "当前设计仍会保留；恢复连接后可以重新发送本轮内容。";
    addMessage(
      "assistant",
      "课程服务暂时无法完成本轮请求。当前设计已保留，请稍后重试。",
      ["连接失败"],
      { meta: "ECE329 Agent" },
    );
    state.quickActions = [message];
    showToast("请求失败，当前设计已保留");
  } finally {
    setBusy(false);
    render();
    saveState();
    dom.chatInput.focus();
  }
}

async function sendToApi(message) {
  if (!state.designId || state.sessionKind === "demo" || state.designId.startsWith("demo_")) {
    return createApiDesign(message);
  }
  const token = sessionStorage.getItem(DESIGN_TOKEN_KEY) || "";
  if (!token) {
    throw new ApiError("Missing design access token", 401, "access_denied");
  }
  const turn = buildTurnRequest(message);
  return apiRequest(`/v1/designs/${encodeURIComponent(state.designId)}/turns`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(turn),
  });
}

async function createApiDesign(message) {
  const request = () => apiRequest("/v1/designs", {
      method: "POST",
      headers: courseAccessHeaders(),
      body: JSON.stringify({ idea: message }),
    });
  try {
    return await request();
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== "access_denied") throw error;
    const accessCode = window.prompt("该网站需要输入课程访问码。请输入老师提供的访问码。", "");
    if (!accessCode) throw error;
    sessionStorage.setItem(ACCESS_CODE_KEY, accessCode.trim());
    return request();
  }
}

function courseAccessHeaders() {
  const accessCode = sessionStorage.getItem(ACCESS_CODE_KEY) || "";
  return accessCode ? { "X-ECE329-Access-Code": accessCode } : {};
}

function clearApiSession() {
  state.designId = null;
  state.sessionKind = null;
  state.stageIndex = 0;
  state.pendingOptionId = null;
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
}

async function reloadApiDesignState() {
  if (!state.designId) return;
  const token = sessionStorage.getItem(DESIGN_TOKEN_KEY) || "";
  if (!token) return;
  try {
    const design = await apiRequest(`/v1/designs/${encodeURIComponent(state.designId)}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
    });
    state.mode = design.interaction_state || state.mode;
    const index = STAGES.findIndex(([id]) => id === design.current_stage);
    if (index >= 0) state.stageIndex = index;
  } catch (error) {
    console.warn("Unable to refresh design state", error);
  }
}

function isAdvanceIntent(message) {
  return /确认.*(下一|进入|完成)|进入下一阶段|继续下一阶段|完成本阶段|完成总结/.test(message);
}

function buildTurnRequest(message) {
  const turn = { message };
  if (state.pendingOptionId) {
    turn.selected_option_id = state.pendingOptionId;
    state.pendingOptionId = null;
  }
  if (state.mode !== "GUIDED_DESIGN" || !isAdvanceIntent(message)) return turn;

  turn.complete_stage = true;
  if (state.stageIndex === 0) {
    const evidence = Array.isArray(state.evidence) ? state.evidence[0] : state.evidence;
    const direction = state.pendingDirection || state.lastStudentInput || message;
    turn.context_patch = {
      idea: {
        phenomenon: evidence?.title || direction,
        main_direction: direction,
        student_confirmed: true,
      },
    };
  } else if (state.stageIndex === STAGES.length - 1) {
    const summary = String(state.pendingSummary || "").trim();
    const sections = (state.summarySections || [])
      .map((section) => String(section).trim())
      .filter((section) => section.length >= 10);
    if (summary.length >= 20 && sections.length >= 2) {
      turn.context_patch = {
        synthesis: {
          student_summary: summary,
          student_summary_sections: sections,
          student_summary_complete: true,
        },
      };
    } else {
      turn.complete_stage = false;
    }
  }
  return turn;
}

function createDemoExplorationScenes(evidence) {
  const frames = [
    {
      title: "让两个对象逐渐靠近",
      picture: "想象把两个相关对象从相距很远慢慢移到彼此附近，并从不同方向观察周围的场或波。原先规则的空间图样会在哪里先弯曲、聚集、抵消或重新排列？",
      extension: "可以把规则外形换成带尖角、弯折或窄缝的结构，看看画面会不会出现新的特征。",
    },
    {
      title: "让材料与边界形成反差",
      picture: "想象保持整体轮廓相近，却把其中一个区域换成另一种材料、边界或终端状态。从界面的一侧走到另一侧时，场的方向、幅度或传播图样会怎样变化？",
      extension: "可以设想带开口的外壳、分层材料或不完全封闭的边界，作为进一步联想。",
    },
    {
      title: "让多个来源在空间中相遇",
      picture: "想象同时存在两个来源或两条传播路径，并缓慢改变它们的相对位置与朝向。在空间中移动观察点，哪里会出现增强、减弱、节点或方向突变？",
      extension: "可以加入第三个对象或不对称扰动，观察原有图样是否仍保持直观的对称性。",
    },
  ];
  return evidence.options.map((direction, index) => {
    const frame = frames[index % frames.length];
    const label = `图景 ${String.fromCharCode(65 + index)}`;
    const nextLabel = `图景 ${String.fromCharCode(65 + ((index + 1) % evidence.options.length))}`;
    return {
      scene_id: `demo_scene_${index + 1}`,
      label,
      title: frame.title,
      course_anchor: direction,
      physical_picture: `${frame.picture} 这幅图景围绕ECE329课上所学的“${direction}”展开。`,
      thinking_prompt: "其中哪一种看似反直觉的场或波变化最值得你继续解释？",
      combination_seed: `也可以把这里的对象、材料或边界与${nextLabel}交换、叠加或重新组合。`,
      illustrative_extension: frame.extension,
      extension_scope: "ILLUSTRATIVE_ONLY_NOT_COURSE_EVIDENCE",
    };
  });
}

function formatDemoExplorationScenes(scenes) {
  return scenes.map((scene) => [
    `${scene.label}｜${scene.title}`,
    scene.physical_picture,
    `启发性延伸：${scene.illustrative_extension}`,
    `可以继续想：${scene.thinking_prompt}`,
    `组合提示：${scene.combination_seed}`,
  ].join("\n")).join("\n\n");
}

function demoBreadthTask() {
  return "哪幅图景触发了你的联想，或者你想怎样组合、替换其中的对象，提出一个自己的ECE329课内设想？";
}

function createDemoResponse(message) {
  const firstTurn = !state.designId;
  const emvrIntent = detectDemoEmvrIntent(message);
  const directEvidence = findDemoKnowledge(message);
  const selectedPriorOption = !firstTurn && state.stageIndex === 0
    ? resolveDemoOptionReference(message, state.quickActions, state.pendingOptionId)
    : null;
  state.pendingOptionId = null;
  let inputCategory = classifyDemoStageOneInput(message, directEvidence);
  if (selectedPriorOption) {
    inputCategory = "COURSE_CONTENT";
  }
  if (
    !firstTurn
    && state.stageIndex === 0
    && state.stageOnePhase !== "BREADTH_EXPLORATION"
    && inputCategory !== "UNREASONABLE_REQUEST"
  ) {
    inputCategory = "COURSE_CONTENT";
  }
  if (emvrIntent !== null && inputCategory !== "UNREASONABLE_REQUEST") {
    inputCategory = "COURSE_CONTENT";
  }
  const noDirection = isDemoNoDirectionRequest(message);
  const emvr = emvrIntent === true && inputCategory !== "UNREASONABLE_REQUEST";
  const evidence = inputCategory === "COURSE_CONTENT"
    ? (directEvidence || state.evidence || FALLBACK_EVIDENCE)
    : (state.evidence || FALLBACK_EVIDENCE);

  if (firstTurn) {
    state.designId = `demo_${Date.now().toString(36)}`;
    state.sessionKind = "demo";
    state.mode = emvr ? "EMVR_DIRECT" : "GUIDED_DESIGN";
    state.evidence = evidence;
    state.notes = [`初始想法：${message}`];
    state.quickActions = evidence.options;
    if (emvr) state.stageIndex = 1;
    const scenes = createDemoExplorationScenes(evidence);
    const sceneText = formatDemoExplorationScenes(scenes);
    let guidedIntroduction;
    if (inputCategory === "UNREASONABLE_REQUEST") {
      guidedIntroduction = "这个请求试图控制课程助手、改变它的工作方式，或让它执行与ECE329实验设计无关的操作，我不能执行。我们把讨论回到ECE329课上学习的电磁场、电磁波和传输线。";
    } else if (noDirection) {
      guidedIntroduction = "暂时没有具体方向也没关系。我们可以先从ECE329课上学习的电磁场、电磁波和传输线中寻找你感兴趣的关系。";
    } else if (inputCategory === "OUT_OF_SCOPE") {
      guidedIntroduction = "你提出的主题不属于ECE329课程的内容范围，因此不适合作为这门课实验设计的核心。ECE329主要学习电磁场、电磁波和传输线，你可以先参考下面三个例子。";
    } else {
      guidedIntroduction = `“${message}”属于ECE329课程相关内容，可以继续从不同的物理关系中展开。现在先不确定变量、公式或实验结构，而是找出你真正感兴趣的物理联系。`;
    }
    return {
      assistant_message: emvr
        ? `已把你的想法整理为Unity VR模拟实验的设计起点，并将优先保留其中与ECE329课程相关的物理现象。接下来会逐步完善学习目标、理论关系、交互对象和观察反馈。`
        : `${guidedIntroduction}\n\n下面不是一组标准答案，而是几幅可以继续改造、交换或组合的物理图景：\n\n${sceneText}\n\n${demoBreadthTask()}`,
      current_stage: STAGES[state.stageIndex][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      quick_actions: evidence.options,
      stage_payload: {
        input_category: inputCategory,
        brainstorm_phase: "BREADTH_EXPLORATION",
        alternative_ideas: evidence.options,
        exploration_scenes: scenes,
        ready_for_next_stage: false,
      },
      warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
      _runtime_source: "demo",
    };
  }

  if (inputCategory !== "UNREASONABLE_REQUEST" && emvrIntent !== null) {
    state.mode = emvrIntent ? "EMVR_DIRECT" : "GUIDED_DESIGN";
  }

  if (inputCategory === "UNREASONABLE_REQUEST") {
    const courseEvidence = FALLBACK_EVIDENCE;
    const scenes = createDemoExplorationScenes(courseEvidence);
    const sceneText = formatDemoExplorationScenes(scenes);
    state.quickActions = courseEvidence.options;
    return {
      assistant_message: `这个请求试图控制课程助手、改变它的工作方式，或让它执行与ECE329实验设计无关的操作，我不能执行。我们把讨论回到ECE329课上学习的电磁场、电磁波和传输线。下面的图景不是固定答案，而是帮助你重新产生课程内的物理联想。\n\n${sceneText}`,
      current_stage: STAGES[state.stageIndex][0],
      interaction_state: state.mode,
      knowledge_references: [courseEvidence],
      quick_actions: courseEvidence.options,
      stage_payload: {
        request_rejected: true,
        input_category: "UNREASONABLE_REQUEST",
        brainstorm_phase: "BREADTH_EXPLORATION",
        alternative_ideas: courseEvidence.options,
        exploration_scenes: scenes,
      },
      warnings: ["当前请求没有改变你的实验设计进度。"],
      request_rejected: true,
      _runtime_source: "demo",
    };
  }

  if (
    selectedPriorOption
    && state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
  ) {
    state.stageOnePhase = "INTEREST_DESCRIPTION";
    state.quickActions = [];
    state.notes.push(`已选择方向：${selectedPriorOption.label}`);
    return {
      assistant_message: `你已经把方向收到了“${selectedPriorOption.label}”。我先不继续列出新选项，因为同一个方向对不同学生可能意味着不同的兴趣。你可以描述让你注意到它的现象、最想解释的物理联系，或者目前仍感到疑惑的地方；不需要写成正式的实验问题。`,
      student_task: "请用自己的话描述：这个方向中什么现象或物理联系最吸引你，以及你最希望进一步弄清什么？",
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_phase: "INTEREST_DESCRIPTION",
        selected_focus: selectedPriorOption.label,
        current_focus: selectedPriorOption.label,
        alternative_ideas: [],
        exploration_scenes: [],
        ready_for_next_stage: false,
      },
      quick_actions: [],
      warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
      _runtime_source: "demo",
    };
  }

  if (
    state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
    && ["INTEREST_DESCRIPTION", "DEPTH_EXPANSION"].includes(state.stageOnePhase)
    && inputCategory === "COURSE_CONTENT"
  ) {
    state.stageOnePhase = "DEPTH_EXPANSION";
    state.quickActions = ["确认当前方向并进入下一阶段"];
    const selectedFocus = state.pendingDirection || evidence.title;
    return {
      assistant_message: `你刚才的描述让“${selectedFocus}”变得更具体了。沿着你的表述继续深入，这里值得关注的不是一个孤立现象，而是场的空间分布、边界条件与可观察变化之间怎样相互联系。这个方向已经开始形成清楚的物理内容，但现在仍不需要提前确定变量、公式或实验装置。`,
      student_task: "请继续用自己的话补充或修正这段理解；如果它已经准确表达你的想法，也可以确认当前方向并进入下一阶段。",
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_phase: "DEPTH_EXPANSION",
        selected_focus: selectedFocus,
        interest_description: message,
        current_focus: `${selectedFocus} → ${message}`,
        alternative_ideas: [],
        exploration_scenes: [],
        ready_for_next_stage: true,
      },
      quick_actions: ["确认当前方向并进入下一阶段"],
      warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
      _runtime_source: "demo",
    };
  }

  const advanceRequested = /确认|进入下一|继续下一|完成本阶段/.test(message);
  if (advanceRequested && state.stageIndex < STAGES.length - 1) {
    state.stageIndex += 1;
    state.notes.push(`已进入阶段${state.stageIndex + 1}：${stageTitle(state.stageIndex)}`);
  }

  const prompt = DEMO_STAGE_PROMPTS[state.stageIndex];
  if (state.mode === "GUIDED_DESIGN" && state.stageIndex === 0 && inputCategory !== "COURSE_CONTENT") {
    const courseEvidence = FALLBACK_EVIDENCE;
    const scenes = createDemoExplorationScenes(courseEvidence);
    const sceneText = formatDemoExplorationScenes(scenes);
    const introduction = inputCategory === "UNREASONABLE_REQUEST"
      ? "这个请求试图控制课程助手、改变它的工作方式，或让它执行与ECE329实验设计无关的操作，我不能执行。我们把讨论回到ECE329课上学习的电磁场、电磁波和传输线。"
      : "你提出的主题不属于ECE329课程的内容范围，因此不适合作为这门课实验设计的核心。ECE329主要学习电磁场、电磁波和传输线，你可以先参考下面三个例子。";
    state.evidence = courseEvidence;
    state.stageOnePhase = "BREADTH_EXPLORATION";
    state.quickActions = courseEvidence.options;
    return {
      assistant_message: `${introduction}\n\n下面不是一组标准答案，而是几幅可以继续改造、交换或组合的物理图景：\n\n${sceneText}\n\n${demoBreadthTask()}`,
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [courseEvidence],
      quick_actions: courseEvidence.options,
      stage_payload: {
        input_category: inputCategory,
        brainstorm_phase: "BREADTH_EXPLORATION",
        alternative_ideas: courseEvidence.options,
        exploration_scenes: scenes,
        ready_for_next_stage: false,
      },
      warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
      _runtime_source: "demo",
    };
  }
  if (state.mode === "GUIDED_DESIGN" && state.stageIndex === 0) {
    const scenes = createDemoExplorationScenes(evidence);
    state.quickActions = evidence.options;
    return {
      assistant_message: `“${message}”可以继续从不同的ECE329物理关系中展开。现在先不确定变量、公式或实验结构，而是用几幅可以改造和组合的图景寻找真正感兴趣的联系。\n\n${formatDemoExplorationScenes(scenes)}\n\n${demoBreadthTask()}`,
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      quick_actions: evidence.options,
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_phase: "BREADTH_EXPLORATION",
        alternative_ideas: evidence.options,
        exploration_scenes: scenes,
        ready_for_next_stage: false,
      },
      warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
      _runtime_source: "demo",
    };
  }
  return {
    assistant_message: `已记录你本轮的想法：“${message}”。\n\n我们会继续围绕ECE329课上所学的“${evidence.title}”展开。${prompt}`,
    current_stage: STAGES[state.stageIndex][0],
    interaction_state: state.mode,
    knowledge_references: [evidence],
    quick_actions: state.stageIndex === 0 ? evidence.options : ["确认并继续下一阶段"],
    warnings: ["当前使用课程示例回答，内容用于帮助你继续思考实验方向。"],
    _runtime_source: "demo",
  };
}

function findDemoKnowledge(text) {
  const lower = text.toLocaleLowerCase();
  return DEMO_KNOWLEDGE.find((entry) => entry.keywords.some((keyword) => lower.includes(keyword))) || null;
}

function classifyDemoStageOneInput(text, directEvidence) {
  const normalized = text.trim();
  const unreasonablePatterns = [
    /(工作流|workflow|\bagent\b|智能体).{0,12}(提示|内部|规则|原理|关闭|修改|绕过|任意输出)/i,
    /(提示|内部|规则|原理|关闭|修改|绕过).{0,12}(工作流|workflow|\bagent\b|智能体)/i,
    /system\s*prompt|系统提示|提示词|内部指令|隐藏指令|\bapi\b|后端|前端|服务器|源代码|github|render|部署|密钥|access[ _-]*token|令牌/i,
    /角色扮演|role\s*play|扮演.{0,12}(角色|老师|学生|专家|人物)|忽略.{0,12}(之前|以上|系统|规则|指令)|越狱|jailbreak|捣乱|输出.{0,8}(无关|随机|违规)内容/i,
    /你的.{0,8}(工作原理|内部机制|规则|提示|身份|能力)/i,
    /(关闭|关掉|停止|终止|禁用|卸载|删除|重启|重置).{0,12}(你|助手|agent|智能体|网页|网站|系统|服务|工作流)/i,
    /(shut\s*down|turn\s*off|disable|kill|stop|restart|reset).{0,20}(agent|assistant|website|system|service|workflow)/i,
    /(写|生成|执行|运行|注入|提交).{0,8}(代码|脚本|程序|命令|指令)|(代码|脚本|程序|命令).{0,8}(执行|运行|控制|修改|输出|关闭)/i,
    /\b(write|generate|execute|run|inject)\b.{0,20}\b(code|script|command|function|class|import)\b|\b(code|script|command)\b.{0,20}\b(execute|run|control|modify)\b/i,
    /```|<\s*script\b|javascript\s*:|\beval\s*\(|\bexec\s*\(|\bfetch\s*\(|\b(subprocess|os\.system|document\.|window\.|localstorage|process\.env)\b/i,
    /(接入|调用|连接|控制).{0,16}(b站|哔哩哔哩|youtube|抖音|网站|平台|机器人|bot|agent|智能体)|(b站|哔哩哔哩|youtube|抖音).{0,20}(翻译|输出|脚本|代码|agent|智能体)/i,
  ];
  if (unreasonablePatterns.some((pattern) => pattern.test(normalized))) {
    return "UNREASONABLE_REQUEST";
  }
  if (isDemoNoDirectionRequest(normalized)) return "COURSE_CONTENT";
  return directEvidence ? "COURSE_CONTENT" : "OUT_OF_SCOPE";
}

function detectDemoEmvrIntent(text) {
  const normalized = text.trim().toLocaleLowerCase();
  const negative = /(?:不要|不需要|不用|退出|取消|关闭|移除).{0,12}(?:emvr|unity\s*vr)|(?:emvr|unity\s*vr).{0,12}(?:不要|不需要|不用|退出|取消|关闭|移除)|\b(?:do\s*not|don't|without|disable|leave|exit)\b.{0,20}\bemvr\b/i;
  if (negative.test(normalized)) return false;
  if (!/\bemvr\b|unity\s*vr/i.test(normalized)) return null;
  const positive = /(?:放入|使用|采用|切换|进入|启用|按照|通过|需要).{0,16}(?:emvr|unity\s*vr)|(?:emvr|unity\s*vr).{0,16}(?:工作流|模式|设计|实验|完善)|\b(?:use|enable|enter|switch\s+to|with)\b.{0,20}\bemvr\b/i;
  return positive.test(normalized) || normalized === "emvr" ? true : null;
}

function isDemoNoDirectionRequest(text) {
  const normalized = text.trim();
  const noDirection = /还没有.{0,6}(方向|想法)|没有.{0,6}(具体|明确).{0,6}(方向|想法)|不知道.{0,10}(研究|选|做什么)|帮我.{0,4}(想|brainstorm)|随便.{0,6}(推荐|举例|给.*方向)/i;
  return !normalized || noDirection.test(normalized);
}

function resolveDemoOptionReference(text, options, selectedOptionId = null) {
  if (!Array.isArray(options) || !options.length) return null;
  const normalizedOptions = options.map(normalizeQuickAction);
  if (selectedOptionId) {
    const selected = normalizedOptions.find((option) => option.optionId === selectedOptionId);
    if (selected) return selected;
  }
  const normalized = text.trim();
  const patterns = [
    /第\s*([一二三123])\s*(?:个|项|类|条|种|方向|例子)/i,
    /第\s*([一二三123])\s*$/i,
    /(?:选|选择|研究|想要|考虑)\s*(?:第\s*)?([一二三123])\s*(?:个|项|类|条|种|方向|例子)/i,
    /(?:选|选择)\s*([123])\s*$/i,
    /(?:上面|刚才|之前).{0,6}([一二三123])\s*(?:个|项|类|条|种|方向|例子)/i,
  ];
  const ordinalMap = { 一: 0, 二: 1, 三: 2 };
  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (!match) continue;
    const index = Object.hasOwn(ordinalMap, match[1])
      ? ordinalMap[match[1]]
      : Number(match[1]) - 1;
    return index >= 0 && index < normalizedOptions.length ? normalizedOptions[index] : null;
  }
  return null;
}

function applyResponse(response, userMessage) {
  if (response.design_access_token) {
    sessionStorage.setItem(DESIGN_TOKEN_KEY, response.design_access_token);
  }
  if (response.design_id) {
    state.designId = response.design_id;
    state.sessionKind = "api";
  }
  state.mode = response.interaction_state || state.mode;

  const stageId = response.current_stage || response.handled_stage;
  const nextIndex = STAGES.findIndex(([id]) => id === stageId);
  if (nextIndex >= 0) state.stageIndex = nextIndex;

  const evidence = extractEvidence(response);
  if (evidence.length) state.evidence = evidence;
  if (state.stageIndex === 0) {
    const inputCategory = response.stage_payload?.input_category;
    if (response.stage_payload?.brainstorm_phase) {
      state.stageOnePhase = response.stage_payload.brainstorm_phase;
    }
    if (!inputCategory || inputCategory === "COURSE_CONTENT") {
      const serverFocus = response.stage_payload?.current_focus
        || response.stage_payload?.current_idea_summary;
      if (typeof serverFocus === "string" && serverFocus.trim()) {
        state.pendingDirection = serverFocus.trim();
      }
    }
  }
  state.quickActions = deriveQuickActions(response);

  const text = composeAssistantText(response);
  const tags = [
    `阶段 ${Math.min(state.stageIndex + 1, STAGES.length)}`,
    ...(response.warnings?.length ? ["含提示"] : []),
  ];
  addMessage("assistant", text, tags, {
    meta: response._runtime_source === "api" ? "ECE329 Agent" : "页面演示",
  });

  if (!state.notes.some((note) => note.includes(userMessage.slice(0, 40)))) {
    state.notes.push(`学生输入：${userMessage.slice(0, 90)}`);
  }

  if (response.visualization) {
    state.visualization = response.visualization;
    dom.chartDescription.textContent = response.visualization.disclaimer || "该图表示理论预测，不是实际测量数据。";
  }
}

function composeAssistantText(response) {
  const base = response.assistant_message || response.message || "Agent已处理当前阶段。";
  const parts = [base];
  const studentTask = typeof response.student_task === "string" ? response.student_task.trim() : "";
  if (studentTask && !base.includes(studentTask)) parts.push(studentTask);
  const warnings = Array.isArray(response.warnings)
    ? response.warnings.filter((item) => typeof item === "string" && item.trim())
    : [];
  if (warnings.length) parts.push(`提示：${warnings.join("；")}`);
  if (response.completion_error) parts.push(`尚未推进：${response.completion_error}`);
  return parts.join("\n\n");
}

function deriveQuickActions(response) {
  if (response.quick_actions) return response.quick_actions;
  if (response.workflow_status === "complete" || response.status === "complete") return [];
  if (state.mode === "EMVR_DIRECT") return ["继续完善下一阶段"];

  if (response.current_stage && response.handled_stage && response.current_stage !== response.handled_stage) {
    return [`开始阶段${state.stageIndex + 1}：${stageTitle(state.stageIndex)}`];
  }

  if (state.stageIndex === 0) {
    const alternatives = (response.stage_payload?.alternative_ideas || [])
      .map((item) => ({
        option_id: item.option_id || null,
        label: item.focus || item.direction,
      }))
      .filter((item) => item.label)
      .slice(0, 3);
    const sceneActions = (response.stage_payload?.exploration_scenes || [])
      .map((scene) => ({
        option_id: scene.course_anchor?.option_id || null,
        label: [scene.label, scene.title].filter(Boolean).join("｜"),
      }))
      .filter((item) => item.label)
      .slice(0, 4);
    const breadthActions = sceneActions.length ? sceneActions : alternatives;
    const phase = response.stage_payload?.brainstorm_phase || "BREADTH_EXPLORATION";
    if (response.stage_payload?.input_category !== "COURSE_CONTENT") {
      return breadthActions;
    }
    if (phase === "BREADTH_EXPLORATION") return breadthActions;
    if (phase === "INTEREST_DESCRIPTION") return [];
    const confirmation = "确认当前方向并进入下一阶段";
    return response.stage_payload?.ready_for_next_stage ? [confirmation] : [];
  }
  if (state.stageIndex === STAGES.length - 1) {
    return String(state.pendingSummary || "").trim().length >= 20
      && (state.summarySections || []).filter((section) => String(section).trim().length >= 10).length >= 2
      ? ["确认完成学生总结"]
      : [];
  }
  return ["确认本阶段并进入下一阶段"];
}

function extractEvidence(response) {
  if (response.knowledge_references) return response.knowledge_references;
  const payload = response.stage_payload || {};
  const concepts = payload.course_references || [];
  const formulas = payload.lecture_formula_candidates || payload.core_equations || [];
  const brainstorm = (payload.alternative_ideas || []).map((item) => {
    const reference = Array.isArray(item.references) ? item.references[0] : null;
    return {
      lecture: reference?.source_title || (item.source_lecture ? `Lecture ${item.source_lecture}` : "Course overview"),
      pages: reference?.pdf_pages || item.source_pages,
      title: item.direction,
      concepts: item.focus,
    };
  });
  return [...concepts, ...formulas, ...brainstorm].slice(0, 4);
}

function setBusy(isBusy) {
  dom.sendButton.disabled = isBusy;
  dom.chatInput.disabled = isBusy;
  dom.sendButton.querySelector("span").textContent = isBusy ? "处理中" : "发送";
}

function autoGrowInput() {
  dom.chatInput.style.height = "auto";
  dom.chatInput.style.height = `${Math.min(dom.chatInput.scrollHeight, 150)}px`;
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    dom.toast.hidden = true;
  }, 2800);
}

function resetDesign() {
  const confirmed = window.confirm("确定开始一个新的实验设计吗？当前浏览器中的对话记录会被清除。");
  if (!confirmed) return;
  const designId = state.designId;
  const token = sessionStorage.getItem(DESIGN_TOKEN_KEY) || "";
  if (apiBase() && designId && token && state.sessionKind === "api") {
    void apiRequest(`/v1/designs/${encodeURIComponent(designId)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    }).catch((error) => console.warn("Unable to delete backend design", error));
  }
  state = initialState();
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
  saveState();
  render();
  dom.chatInput.focus();
  showToast("已创建新的本地设计会话");
}

function drawChart() {
  const canvas = dom.chart;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 19, right: 8, bottom: 25, left: 31 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const visualization = state.visualization || {};
  const series = visualization.series?.[0] || {};
  const apiPoints = normalizeChartPoints(series.points);
  const hasApiPoints = apiPoints.length >= 2;
  const xAxis = formatAxisLabel(visualization.x_axis, "主要自变量");
  const yAxis = formatAxisLabel(visualization.y_axis, "响应");

  canvas.dataset.source = hasApiPoints ? "api" : "demo";
  dom.chartLegendLabel.textContent = series.label || (hasApiPoints ? "本次理论数据" : "理论预测");
  dom.chartParameter.disabled = hasApiPoints;
  dom.chartParameter.title = hasApiPoints ? "当前曲线使用本次理论预测的数据点；调整条件后需重新提交才能更新。" : "调整本地示意曲线参数";
  if (visualization.disclaimer) dom.chartDescription.textContent = visualization.disclaimer;

  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#dfe4e2";
  ctx.lineWidth = 1;
  ctx.font = "9px system-ui";
  ctx.fillStyle = "#7b878f";

  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#aeb8b6";
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();

  ctx.fillText(yAxis, 2, pad.top + 2);
  ctx.fillText(xAxis, Math.max(pad.left, width - ctx.measureText(xAxis).width - 4), height - 7);

  const parameter = Number(dom.chartParameter.value);
  dom.parameterValue.value = hasApiPoints ? "理论" : parameter.toFixed(2);
  const gradient = ctx.createLinearGradient(pad.left, 0, width - pad.right, 0);
  gradient.addColorStop(0, "#157f78");
  gradient.addColorStop(1, "#74b7d9");
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2.4;
  ctx.lineJoin = "round";
  ctx.beginPath();

  if (hasApiPoints) {
    const xs = apiPoints.map((point) => point.x);
    const ys = apiPoints.map((point) => point.y);
    const xMin = Math.min(...xs);
    const xSpan = Math.max(Math.max(...xs) - xMin, Number.EPSILON);
    const yMin = Math.min(...ys);
    const ySpan = Math.max(Math.max(...ys) - yMin, Number.EPSILON);
    apiPoints.forEach((point, index) => {
      const x = pad.left + ((point.x - xMin) / xSpan) * plotW;
      const y = pad.top + (1 - (point.y - yMin) / ySpan) * plotH;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
  } else {
    const points = 90;
    for (let i = 0; i < points; i += 1) {
      const ratio = i / (points - 1);
      const x = pad.left + ratio * plotW;
      const response = 0.48 + 0.34 * Math.sin(ratio * Math.PI * 2 * parameter) * Math.exp(-ratio * 0.22);
      const y = pad.top + (1 - response) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
  }
  ctx.stroke();
}

function normalizeChartPoints(points) {
  if (!Array.isArray(points)) return [];
  return points
    .map((point) => {
      const x = Array.isArray(point) ? Number(point[0]) : Number(point?.x);
      const y = Array.isArray(point) ? Number(point[1]) : Number(point?.y);
      return { x, y };
    })
    .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
}

function formatAxisLabel(axis, fallback) {
  if (!axis?.label) return fallback;
  return axis.unit && axis.unit !== "由当前设计定义" ? `${axis.label} (${axis.unit})` : axis.label;
}

dom.chatForm.addEventListener("submit", handleSubmit);
dom.chatInput.addEventListener("input", () => {
  state.pendingOptionId = null;
  autoGrowInput();
});
dom.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dom.chatForm.requestSubmit();
  }
});
dom.resetButton.addEventListener("click", resetDesign);
dom.chartParameter.addEventListener("input", drawChart);
window.addEventListener("resize", drawChart);

render();
checkConnection();
