"use strict";

const CONFIG = window.ECE329_CONFIG || { API_BASE_URL: "", REQUEST_TIMEOUT_MS: 70000 };
const STORAGE_KEY = "ece329-lab-studio-session-v1";
const DESIGN_TOKEN_KEY = "ece329-design-access-token-v1";
const ACCESS_CODE_KEY = "ece329-course-access-code-v1";

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
    conceptId: "lecture_34",
    formulaId: "load_reflection_phasor",
    lecture: "Lecture 34",
    pages: "289–296",
    title: "Line impedance, generalized reflection coefficient, and Smith Chart",
    concepts: "负载反射系数、广义反射系数、线路阻抗、归一化阻抗、Smith Chart",
    formula: "Γ_L=(Z_L-Z₀)/(Z_L+Z₀); Γ(d)=Γ_L e^{-j2βd}",
    formulaPages: "289–290",
    options: ["沿线路移动并观察Γ相位旋转", "比较阻抗与反射系数的双线性变换", "在Smith Chart上跟踪等驻波比圆"],
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
    options: ["改变正交分量相位差观察偏振轨迹", "比较线偏振与圆偏振", "改变观察位置比较矢量旋转"],
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
    options: ["改变频率并观察趋肤深度", "改变电导率并观察穿透深度", "比较介质与良导体中的衰减"],
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
    options: ["改变极板距离观察电容", "改变介电参数观察电容", "比较平行板与同轴结构"],
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
    options: ["改变磁通变化率观察电动势", "改变线圈方向观察符号", "比较运动电动势与时变磁通"],
  },
];

const FALLBACK_EVIDENCE = {
  lecture: "Course overview",
  pages: "10–12",
  title: "ECE329 course blocks",
  concepts: "Electrostatics；Magnetism；Electromagnetics, waves and transmission lines",
  formula: null,
  formulaPages: null,
  options: ["从静电场、电势、介质或电容开始", "从磁力、感应或电感开始", "从电磁波、偏振、反射或传输线开始"],
};

const DEMO_STAGE_PROMPTS = [
  "下面三个方向都来自讲义目录。哪一个最接近你真正想让学生探索的现象？",
  "你希望把哪一个讲义概念作为实验的主要理论核心？",
  "你最希望学生通过这个实验获得哪一种能力？",
  "你希望实验主要改变哪一个因素？",
  "哪一个讲义公式最直接连接自变量和观察量？",
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
        text: "欢迎来到 ECE329 Lab Studio。我们先从讲义中的概念出发探索想法，不急着写完整方案。\n\n请描述一个你感兴趣的电磁现象，或者告诉我你还没有具体方向。",
        meta: "ECE329 Design Guide",
        tags: ["阶段 1", "Lecture-grounded"],
      },
    ],
    evidence: null,
    visualization: null,
    quickActions: ["传输线驻波", "电磁波偏振", "导体中的衰减"],
    notes: [],
    pendingDirection: null,
    pendingSummary: null,
    lastStudentInput: null,
  };
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (saved && Array.isArray(saved.messages) && Number.isInteger(saved.stageIndex)) {
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
    setConnectionState("demo", "演示模式 · API未连接");
    dom.offlineNotice.hidden = false;
    return;
  }
  try {
    await apiRequest("/health", { method: "GET" });
    setConnectionState("online", "Agent API 已连接");
    dom.offlineNotice.hidden = true;
  } catch (error) {
    setConnectionState("error", "API连接失败");
    dom.offlineNotice.hidden = false;
    dom.offlineNotice.querySelector("strong").textContent = "离线模式";
    dom.offlineNotice.querySelector("span").textContent = "无法连接已配置的 API，暂时使用本地演示回答。";
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
  (state.quickActions || []).forEach((label) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-action";
    button.textContent = label;
    button.addEventListener("click", () => {
      dom.chatInput.value = label;
      autoGrowInput();
      dom.chatInput.focus();
    });
    dom.quickActions.append(button);
  });
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

  if (state.stageIndex === 0 && !isAdvanceIntent(message)) {
    state.pendingDirection = message;
  }
  if (state.stageIndex === STAGES.length - 1 && !isAdvanceIntent(message)) {
    state.pendingSummary = message;
  }
  state.lastStudentInput = message;
  addMessage("user", message);
  dom.chatInput.value = "";
  autoGrowInput();
  setBusy(true);
  showTyping();

  try {
    let response;
    if (apiBase() && connectionState === "checking") {
      await checkConnection();
    }
    if (apiBase() && connectionState === "online") {
      response = await sendToApi(message);
      response._runtime_source = "api";
    } else {
      await wait(420);
      response = createDemoResponse(message);
    }
    hideTyping();
    applyResponse(response, message);
  } catch (error) {
    hideTyping();
    if (error instanceof ApiError && ["session_not_found", "access_denied"].includes(error.code)) {
      const hadDesign = Boolean(state.designId);
      clearApiSession();
      setConnectionState("online", "Agent API 已连接");
      const guidance = hadDesign
        ? "原后端会话已失效或访问令牌不匹配。网页已清除旧会话；请重新输入实验想法开始新设计。"
        : "访问码不正确或尚未提供。请再次发送，并输入后端管理员提供的课程访问码。";
      addMessage("assistant", guidance, ["需要重新连接"], { meta: "ECE329 Agent" });
      state.quickActions = ["传输线驻波", "电磁波偏振", "导体中的衰减"];
      showToast("未切换演示模式，请按提示重新连接");
      return;
    }
    setConnectionState("error", "API连接失败");
    dom.offlineNotice.hidden = false;
    dom.offlineNotice.querySelector("strong").textContent = "离线模式";
    dom.offlineNotice.querySelector("span").textContent = "Agent API请求失败，已自动切换为本地演示回答。";
    clearApiSession();
    const fallback = createDemoResponse(message);
    fallback.assistant_message = `Agent API请求失败（${error.message}），本轮已切换为本地演示。\n\n${fallback.assistant_message}`;
    hideTyping();
    applyResponse(fallback, message);
    showToast("API请求失败，已切换到演示模式");
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
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
}

function isAdvanceIntent(message) {
  return /确认.*(下一|进入|完成)|进入下一阶段|继续下一阶段|完成本阶段|完成总结/.test(message);
}

function buildTurnRequest(message) {
  const turn = { message };
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
    if (summary.length >= 20) {
      turn.context_patch = {
        synthesis: {
          student_summary: summary,
          student_summary_complete: true,
        },
      };
    } else {
      turn.complete_stage = false;
    }
  }
  return turn;
}

function createDemoResponse(message) {
  const lower = message.toLocaleLowerCase();
  const firstTurn = !state.designId;
  const emvr = lower.includes("emvr");
  const evidence = matchDemoKnowledge(message);

  if (firstTurn) {
    state.designId = `demo_${Date.now().toString(36)}`;
    state.sessionKind = "demo";
    state.mode = emvr ? "EMVR_DIRECT" : "GUIDED_DESIGN";
    state.evidence = evidence;
    state.notes = [`初始想法：${message}`];
    state.quickActions = evidence.options;
    if (emvr) state.stageIndex = 1;
    return {
      assistant_message: emvr
        ? `已把你的想法映射到讲义中的“${evidence.title}”，并整理为Unity VR模拟实验的设计起点。当前是页面演示回答；连接API后，将由Agent直接完善每个阶段。`
        : `我在讲义中找到了与“${evidence.title}”相关的入口。先停留在想法探索阶段，下面三个方向都来自对应讲次。\n\n${evidence.options.map((option, index) => `${index + 1}. ${option}`).join("\n")}\n\n${DEMO_STAGE_PROMPTS[0]}`,
      current_stage: STAGES[state.stageIndex][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      quick_actions: evidence.options,
      warnings: ["本回答来自静态页面演示器，不是远程Agent。"],
      _runtime_source: "demo",
    };
  }

  const advanceRequested = /确认|进入下一|继续下一|完成本阶段/.test(message);
  if (advanceRequested && state.stageIndex < STAGES.length - 1) {
    state.stageIndex += 1;
    state.notes.push(`已进入阶段${state.stageIndex + 1}：${stageTitle(state.stageIndex)}`);
  }

  const prompt = DEMO_STAGE_PROMPTS[state.stageIndex];
  return {
    assistant_message: `已记录你本轮的想法：“${message}”。\n\n当前仍以讲义中的“${evidence.title}”作为课程依据。${prompt}`,
    current_stage: STAGES[state.stageIndex][0],
    interaction_state: state.mode,
    knowledge_references: [evidence],
    quick_actions: state.stageIndex === 0 ? evidence.options : ["确认并继续下一阶段"],
    warnings: ["本回答来自静态页面演示器，不是远程Agent。"],
    _runtime_source: "demo",
  };
}

function matchDemoKnowledge(text) {
  const lower = text.toLocaleLowerCase();
  const match = DEMO_KNOWLEDGE.find((entry) => entry.keywords.some((keyword) => lower.includes(keyword)));
  return match || state.evidence || FALLBACK_EVIDENCE;
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
      .map((item) => item.focus)
      .filter(Boolean)
      .slice(0, 3);
    return [...alternatives, "确认当前方向并进入下一阶段"];
  }
  if (state.stageIndex === STAGES.length - 1) {
    return String(state.pendingSummary || "").trim().length >= 20
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
  const brainstorm = (payload.alternative_ideas || []).map((item) => ({
    lecture: item.source_lecture ? `Lecture ${item.source_lecture}` : "Course overview",
    pages: item.source_pages,
    title: item.direction,
    concepts: item.focus,
  }));
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
  dom.chartLegendLabel.textContent = series.label || (hasApiPoints ? "API理论数据" : "理论预测");
  dom.chartParameter.disabled = hasApiPoints;
  dom.chartParameter.title = hasApiPoints ? "当前曲线使用API返回的数据点，参数调整应由后端重新计算。" : "调整本地示意曲线参数";
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
  dom.parameterValue.value = hasApiPoints ? "API" : parameter.toFixed(2);
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
dom.chatInput.addEventListener("input", autoGrowInput);
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
