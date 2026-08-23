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
  ["IDEA_BRAINSTORMING", "想法探索与大纲雏形"],
  ["COURSE_MAPPING_AND_DIRECTION", "课程映射"],
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

const IDEA_DEVELOPMENT_STAGE_IDS = Object.freeze(STAGES.slice(0, 7).map(([id]) => id));
const WORKFLOW_GROUPS = Object.freeze([
  {
    id: "EXPERIMENT_IDEA_DEVELOPMENT",
    title: "实验想法完善",
    stageIds: IDEA_DEVELOPMENT_STAGE_IDS,
  },
  ...STAGES.slice(7).map(([id, title]) => ({ id, title, stageIds: [id] })),
]);

const EMVR_STAGE_TITLES = Object.freeze({
  CONCEPTUAL_OR_VR_SETUP: "Unity VR模拟实验设计",
  STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "EMVR方案汇总",
});

const DEMO_KNOWLEDGE = [
  {
    keywords: ["静电", "电场线", "电荷", "场源", "高斯", "电通量"],
    conceptId: "lecture_02",
    formulaId: null,
    lecture: "Lecture 2",
    pages: "22–29",
    title: "Static electric fields — Coulomb's and Gauss's laws",
    concepts: "库仑场、场叠加、电通量、高斯定律与线电荷",
    formula: null,
    formulaPages: null,
    options: ["电荷分布与空间电场形状的关系", "局部场分布与闭合面通量的关系", "源的对称性与电场方向的关系"],
  },
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

const DEMO_BASELINE_COMPARISONS = [
  {
    comparison_id: "electrostatic_source_polarity_pair",
    triggerGroups: [["两个源", "两源", "两个电荷", "电荷源"], ["电场", "电场线", "静电", "电荷"]],
    cases: ["同种电荷", "异种电荷"],
    case_aliases: { 同种电荷: ["同号电荷", "同性电荷"], 异种电荷: ["异号电荷", "相反电荷"] },
    reason: "两种源极性关系共同构成观察电场叠加与重排的基础对照。",
    course_concept_ids: ["lecture_02"],
  },
  {
    comparison_id: "electrostatic_material_class_pair",
    triggerGroups: [["导体", "介质", "材料", "极化"], ["电场", "电场线", "边界", "内部场"]],
    cases: ["导体情形", "介质情形"],
    case_aliases: { 导体情形: ["导体", "金属"], 介质情形: ["介质", "电介质"] },
    reason: "导体与介质具有不同的静电响应，是比较材料如何改变场分布的基础参照。",
    course_concept_ids: ["lecture_08", "lecture_09"],
  },
  {
    comparison_id: "induced_emf_mechanism_pair",
    triggerGroups: [["感应电动势", "电磁感应", "法拉第", "磁通"], ["运动", "变化", "导体", "线圈"]],
    cases: ["磁通随时间变化", "导体运动切割磁场"],
    case_aliases: { 磁通随时间变化: ["变压器电动势", "时变磁通"], 导体运动切割磁场: ["运动电动势", "导体运动"] },
    reason: "时变磁通与导体运动是课程中理解感应电动势的两类基础机制。",
    course_concept_ids: ["lecture_14"],
  },
  {
    comparison_id: "transmission_line_termination_set",
    triggerGroups: [["传输线", "线路", "负载"], ["反射", "驻波", "终端", "端接", "负载"]],
    cases: ["匹配负载", "开路负载", "短路负载"],
    case_aliases: { 匹配负载: ["匹配", "匹配端"], 开路负载: ["开路", "开路端"], 短路负载: ["短路", "短路端"] },
    reason: "匹配、开路和短路端接构成观察传输线反射与驻波的常用基础参照。",
    course_concept_ids: ["lecture_28", "lecture_29", "lecture_31"],
  },
  {
    comparison_id: "transmission_line_loss_pair",
    triggerGroups: [["传输线", "线路", "传播常数"], ["损耗", "衰减", "有损", "无损"]],
    cases: ["无损线路", "有损线路"],
    case_aliases: { 无损线路: ["无损传输线", "无损"], 有损线路: ["有损传输线", "有损"] },
    reason: "无损与有损模型是理解线路传播常数和衰减的基础参照。",
    course_concept_ids: ["lecture_27", "lecture_39"],
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
  "继续把已经选定的方向扩展成实验大纲雏形。",
  "下面由课程助手展示这个实验想法与ECE329课程内容的主要对应关系。",
  "你最希望通过这个实验获得哪一种能力？",
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

const DEMO_GUIDED_STAGE_ENTRY_QUESTIONS = Object.freeze({
  VARIABLES_AND_CONDITIONS: "先不急着列完整变量表。按照你的理解，这个实验中哪些量应该主动改变、哪些现象需要观察，又有哪些条件应该保持不变？可以先说你认为最重要的部分。",
  CONCEPTUAL_PROCEDURE: "先不急着写标准流程。你认为在这个实验中，从建立比较基准到改变条件、观察现象和比较结果，需要经历哪些关键环节？请先按自己的思路描述。",
  EXPECTED_DATA_VISUALIZATION: "在生成理论预测窗口前，你希望窗口重点呈现哪些量之间的关系，或者最希望从图中看清哪一种变化？",
  RESULT_INTERPRETATION: "对于这个实验可能出现的结果，你认为哪些现象最需要解释，又会先从什么课程关系寻找原因？",
  DESIGN_VALUE_AND_LIMITATIONS: "请先按你的判断描述：这个实验最有价值的学习收获是什么，又有哪些理想化条件、展示方式或设计边界可能限制结论？",
  STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "请先用两到三句话写出这个实验想研究什么、为什么值得研究，以及它与ECE329课程内容有什么联系。",
});

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
    stageOneSelectedRelations: [],
    stageOneCorePhenomenon: null,
    stageOneStandardComparisons: [],
    experimentOutlineSeed: null,
    ideaDevelopmentStatus: null,
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
  const currentGroupIndex = workflowGroupIndex(state.stageIndex);
  WORKFLOW_GROUPS.forEach((group, groupIndex) => {
    const stageIndexes = group.stageIds.map((id) => STAGES.findIndex(([stageId]) => stageId === id));
    const firstIndex = Math.min(...stageIndexes);
    const lastIndex = Math.max(...stageIndexes);
    const item = document.createElement("li");
    item.className = "stage-item";
    item.dataset.stageId = group.id;
    if (state.stageIndex > lastIndex) item.classList.add("complete");
    if (state.stageIndex >= firstIndex && state.stageIndex <= lastIndex) item.classList.add("current");
    item.setAttribute("aria-current", groupIndex === currentGroupIndex ? "step" : "false");

    const number = document.createElement("span");
    number.className = "stage-number";
    number.textContent = state.stageIndex > lastIndex ? "✓" : String(groupIndex + 1);

    const label = document.createElement("span");
    label.className = "stage-label";
    label.textContent = groupTitle(group, firstIndex);

    item.append(number, label);
    if (group.stageIds.length > 1) {
      const substeps = document.createElement("ol");
      substeps.className = "stage-substeps";
      group.stageIds.forEach((id, substepIndex) => {
        const index = STAGES.findIndex(([stageId]) => stageId === id);
        const substep = document.createElement("li");
        substep.className = "stage-substep";
        const facet = state.ideaDevelopmentStatus?.facets?.find((entry) => (
          entry.facet_id === ideaFacetId(id)
        ));
        if (facet?.status === "CLEAR") substep.classList.add("complete");
        if (facet?.status !== "CLEAR") substep.classList.add("missing");
        if (facet?.facet_id === state.ideaDevelopmentStatus?.active_facet_id) {
          substep.classList.add("current");
        }
        const marker = facet?.status === "CLEAR" ? "✓" : (
          facet?.facet_id === state.ideaDevelopmentStatus?.active_facet_id ? "→" : "○"
        );
        substep.textContent = `${marker} ${facet?.title || stageTitle(index)}`;
        substeps.append(substep);
      });
      item.append(substeps);
    }
    dom.stageList.append(item);
  });

  const progress = Math.round((currentGroupIndex / (WORKFLOW_GROUPS.length - 1)) * 100);
  dom.stageCounter.textContent = `阶段 ${currentGroupIndex + 1} / ${WORKFLOW_GROUPS.length}`;
  dom.progressPercent.textContent = `${progress}%`;
  dom.progressBar.style.width = `${progress}%`;
  dom.currentStageTitle.textContent = currentWorkspaceTitle(state.stageIndex);
}

function workflowGroupIndex(stageIndex) {
  const stageId = STAGES[stageIndex]?.[0];
  const index = WORKFLOW_GROUPS.findIndex((group) => group.stageIds.includes(stageId));
  return index >= 0 ? index : 0;
}

function groupTitle(group, firstStageIndex) {
  return group.stageIds.length > 1 ? group.title : stageTitle(firstStageIndex);
}

function currentWorkspaceTitle(stageIndex) {
  const group = WORKFLOW_GROUPS[workflowGroupIndex(stageIndex)];
  if (group.stageIds.length > 1) {
    const active = state.ideaDevelopmentStatus?.facets?.find((facet) => (
      facet.facet_id === state.ideaDevelopmentStatus?.active_facet_id
    ));
    return active ? `${group.title} · 当前缺口：${active.title}` : group.title;
  }
  return stageTitle(stageIndex);
}

function ideaFacetId(stageId) {
  const mapping = {
    IDEA_BRAINSTORMING: "direction_outline",
    COURSE_MAPPING_AND_DIRECTION: "course_mapping",
    LEARNING_OBJECTIVES: "learning_objective",
    RESEARCH_QUESTION: "research_question",
    THEORETICAL_FRAMEWORK: "theoretical_framework",
    HYPOTHESIS: "hypothesis",
    CONCEPTUAL_OR_VR_SETUP: "conceptual_structure",
  };
  return mapping[stageId] || null;
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
  const retainedMessages = state.messages;
  state = { ...initialState(), messages: retainedMessages };
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
  saveState();
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
  const normalized = String(message || "").trim();
  const blockedTransition = /(?:先|暂时)?(?:不|别)(?:要|想|用)?[\s，,。；;！!]*(?:现在|马上|再)?[\s，,。；;！!]*(?:继续|推进|往下|进入|下一(?:步|阶段|部分|环节))|(?:不能|无法|没法|没能).{0,8}(?:继续|推进|往下|进入|下一(?:步|阶段|部分|环节))|(?:为什么|怎么|为何).{0,16}(?:还没|没有|不能|无法|没能).{0,12}(?:继续|推进|进入|下一(?:步|阶段|部分|环节))|(?:继续|推进|进入|下一(?:步|阶段|部分|环节)).{0,24}(?:失败|不了|没反应|没进入|没有进入|重复|卡住|卡在)/.test(normalized);
  if (blockedTransition) return false;
  const standardTransition = /确认.*(下一|进入|继续|完成)|进入下一阶段|继续下一阶段|继续小点|完成本阶段|完成总结/.test(message);
  const ideaTransition = /(?:想法|方向|大纲).{0,12}(?:已)?(?:完善|完成|确认|没问题).{0,16}(?:进入|继续).{0,12}(?:变量与条件|下一阶段)|(?:可以|请|现在|直接|确认|同意|接受|准备好).{0,8}(?:进入|继续).{0,12}(?:变量与条件|下一阶段)|(?:进入|继续).{0,12}变量与条件/.test(message);
  const semanticTransition = /下一(?:步|阶段|部分|环节)|(?:进入|转到|切换到|前往|开始).{0,10}(?:后面|后续|变量与条件)|(?:往下走|往下进行|继续推进|推进到|推进至)/.test(normalized);
  const shortTransition = /^(?:好的?|可以|行|没问题|确认|同意|接受)?[\s，,。；;！!]*(?:(?:那|那么)[\s，,。；;！!]*)?(?:我们[\s，,。；;！!]*)?(?:就[\s，,。；;！!]*)?(?:继续|推进)(?:吧|了|呀|啊)?[。！!]*$/.test(normalized);
  const completedIdeaConfirmation = state.stageIndex === 0
    && state.ideaDevelopmentStatus?.complete === true
    && /^(?:好的?|可以(?:了)?|行|没问题|确认|同意|接受|就这样(?:吧)?|完成(?:了)?|没有(?:要|需要|什么)?(?:改|修改|补充)(?:的)?(?:了)?)[。！!]*$/.test(normalized);
  return standardTransition || ideaTransition || semanticTransition || shortTransition || completedIdeaConfirmation;
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
  let guidedStageEntered = false;
  const emvrIntent = detectDemoEmvrIntent(message);
  const directEvidence = findDemoKnowledge(message);
  const selectedPriorOption = !firstTurn && state.stageIndex === 0
    ? resolveDemoOptionReference(message, state.quickActions, state.pendingOptionId)
    : null;
  const combinedSceneRelations = !firstTurn && state.stageIndex === 0
    ? resolveDemoSceneCombination(message, (state.evidence || FALLBACK_EVIDENCE).options)
    : [];
  state.pendingOptionId = null;
  let inputCategory = classifyDemoStageOneInput(message, directEvidence);
  if (selectedPriorOption || combinedSceneRelations.length) {
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
  const advanceRequested = isAdvanceIntent(message);

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
      warnings: [],
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
    state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
    && state.ideaDevelopmentStatus
    && !advanceRequested
  ) {
    updateDemoIdeaDevelopmentStatus(state.ideaDevelopmentStatus, message);
    const status = state.ideaDevelopmentStatus;
    const clarified = status.last_clarified_facet_ids;
    return {
      assistant_message: `${clarified.length ? demoIdeaAcknowledgement(message, clarified, status) : demoStudentFacingRetry(status)}\n\n${demoStudentFacingNextTurn(status)}`,
      student_task: null,
      current_stage: STAGES[0][0],
      handled_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_activity: "IDEA_COMPLETENESS_REVIEW",
        brainstorm_phase: "IDEA_COMPLETENESS_REVIEW",
        idea_development_status: status,
        ready_for_next_stage: status.complete,
        alternative_ideas: [],
        exploration_scenes: [],
      },
      quick_actions: status.complete ? ["确认想法完善并进入变量与条件"] : [],
      warnings: [],
      _runtime_source: "demo",
    };
  }

  if (
    combinedSceneRelations.length
    && state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
  ) {
    state.stageOnePhase = "INTEREST_DESCRIPTION";
    state.stageOneSelectedRelations = combinedSceneRelations;
    state.stageOneCorePhenomenon = null;
    state.stageOneStandardComparisons = [];
    state.pendingDirection = combinedSceneRelations.join(" + ");
    state.quickActions = [];
    return {
      assistant_message: `你组合的课程关系已经分别保留：${combinedSceneRelations.join("；")}。接下来只需要说明你希望这几条关系共同解释什么核心现象；它们不会在后续描述中被拆成二选一。`,
      student_task: "请用自己的话描述这个组合中你最想理解的核心现象。",
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_phase: "INTEREST_DESCRIPTION",
        selected_course_relations: combinedSceneRelations,
        combination_intent: combinedSceneRelations.length > 1,
        alternative_ideas: [],
        exploration_scenes: [],
        ready_for_next_stage: false,
      },
      quick_actions: [],
      warnings: ["组合中的每条课程关系都会继续保留。"],
      _runtime_source: "demo",
    };
  }

  if (
    selectedPriorOption
    && state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
  ) {
    state.stageOnePhase = "INTEREST_DESCRIPTION";
    state.stageOneSelectedRelations = [selectedPriorOption.label];
    state.stageOneCorePhenomenon = null;
    state.stageOneStandardComparisons = [];
    state.pendingDirection = selectedPriorOption.label;
    state.quickActions = [];
    state.notes.push(`已选择方向：${selectedPriorOption.label}`);
    return {
      assistant_message: `你已经把方向收到了“${selectedPriorOption.label}”。我先不继续列出新选项，因为同一个方向可能对应不同的兴趣。你可以描述让你注意到它的现象、最想解释的物理联系，或者目前仍感到疑惑的地方；不需要写成正式的实验问题。`,
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
      warnings: [],
      _runtime_source: "demo",
    };
  }

  if (
    state.mode === "GUIDED_DESIGN"
    && state.stageIndex === 0
    && ["INTEREST_DESCRIPTION", "DEPTH_EXPANSION"].includes(state.stageOnePhase)
    && inputCategory === "COURSE_CONTENT"
    && !advanceRequested
  ) {
    state.stageOnePhase = "DEPTH_EXPANSION";
    state.quickActions = [];
    if (!state.stageOneCorePhenomenon) state.stageOneCorePhenomenon = message;
    const selectedRelations = state.stageOneSelectedRelations?.length
      ? state.stageOneSelectedRelations
      : [state.pendingDirection || evidence.title];
    const selectedFocus = state.stageOneCorePhenomenon;
    const inferredComparisons = inferDemoStandardComparisons(
      `${selectedRelations.join(" ")} ${state.stageOneCorePhenomenon} ${message}`,
    );
    const knownIds = new Set(
      (state.stageOneStandardComparisons || []).map((item) => item.comparison_id),
    );
    state.stageOneStandardComparisons = [
      ...(state.stageOneStandardComparisons || []),
      ...inferredComparisons.filter((item) => !knownIds.has(item.comparison_id)),
    ];
    state.stageOneStandardComparisons = updateDemoStandardComparisonDecisions(
      message,
      state.stageOneStandardComparisons,
    );
    const comparisonSentence = formatDemoStandardComparisons(
      state.stageOneStandardComparisons,
    );
    state.pendingDirection = `${state.stageOneCorePhenomenon}（${selectedRelations.join("；")}）`;
    state.experimentOutlineSeed = buildDemoExperimentOutlineSeed(
      state.stageOneCorePhenomenon,
      selectedRelations,
      state.stageOneStandardComparisons,
    );
    state.ideaDevelopmentStatus = buildDemoIdeaDevelopmentStatus(
      state.experimentOutlineSeed,
      `${state.pendingDirection || ""} ${state.stageOneCorePhenomenon || ""} ${message}`,
    );
    const outlineText = formatDemoExperimentOutlineSeed(state.experimentOutlineSeed);
    const comparisonPrefix = comparisonSentence ? `${comparisonSentence}\n\n` : "";
    return {
      assistant_message: `${comparisonPrefix}${outlineText}\n\n${demoStudentFacingNextTurn(state.ideaDevelopmentStatus, true)}`,
      student_task: null,
      current_stage: STAGES[0][0],
      interaction_state: state.mode,
      knowledge_references: [evidence],
      stage_payload: {
        input_category: "COURSE_CONTENT",
        brainstorm_phase: "DEPTH_EXPANSION",
        selected_focus: selectedFocus,
        selected_course_relations: selectedRelations,
        combination_intent: selectedRelations.length > 1,
        standard_comparisons: state.stageOneStandardComparisons,
        experiment_outline_seed: state.experimentOutlineSeed,
        idea_development_status: state.ideaDevelopmentStatus,
        core_phenomenon: state.stageOneCorePhenomenon,
        interest_description: message,
        current_focus: `${selectedFocus} → ${message}`,
        alternative_ideas: [],
        exploration_scenes: [],
        ready_for_next_stage: true,
      },
      quick_actions: state.ideaDevelopmentStatus.complete
        ? ["确认想法完善并进入变量与条件"]
        : [],
      warnings: [],
      _runtime_source: "demo",
    };
  }

  if (advanceRequested && state.stageIndex < STAGES.length - 1) {
    if (state.stageIndex === 0) {
      state.stageOneStandardComparisons = updateDemoStandardComparisonDecisions(
        message,
        state.stageOneStandardComparisons || [],
        true,
      );
    }
    if (state.stageIndex === 0 && state.ideaDevelopmentStatus?.complete) {
      state.stageIndex = IDEA_DEVELOPMENT_STAGE_IDS.length;
    } else if (state.stageIndex === 0 && state.ideaDevelopmentStatus) {
      return {
        assistant_message: `这个实验想法还有一部分需要说明清楚，因此现在还不能进入“变量与条件”。\n\n${demoStudentFacingNextTurn(state.ideaDevelopmentStatus)}`,
        student_task: null,
        current_stage: STAGES[0][0],
        handled_stage: STAGES[0][0],
        interaction_state: state.mode,
        stage_payload: {
          idea_development_status: state.ideaDevelopmentStatus,
          ready_for_next_stage: false,
        },
        quick_actions: [],
        warnings: ["实验想法的必要内容尚未全部明确。"],
        _runtime_source: "demo",
      };
    } else {
      state.stageIndex += 1;
    }
    guidedStageEntered = state.mode === "GUIDED_DESIGN";
    state.notes.push(`已进入${currentWorkspaceTitle(state.stageIndex)}`);
  }

  const prompt = DEMO_STAGE_PROMPTS[state.stageIndex];
  if (guidedStageEntered) {
    const [stageId] = STAGES[state.stageIndex];
    const preservedIdea = state.stageOneCorePhenomenon || state.pendingDirection || "前面已经完善的实验想法";
    return {
      assistant_message: `现在进入“${currentWorkspaceTitle(state.stageIndex)}”。前面确定的实验方向已经保留：${preservedIdea}。\n\n${DEMO_GUIDED_STAGE_ENTRY_QUESTIONS[stageId] || "请先用自己的话描述你对当前部分的想法；我会在这个基础上继续帮你完善。"}`,
      student_task: null,
      current_stage: stageId,
      handled_stage: stageId,
      interaction_state: state.mode,
      stage_payload: {
        guided_entry: true,
        awaiting_student_description: true,
        preserved_idea_summary: preservedIdea,
      },
      quick_actions: [],
      warnings: [],
      _runtime_source: "demo",
    };
  }
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
      warnings: [],
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
      warnings: [],
      _runtime_source: "demo",
    };
  }
  return {
    assistant_message: `已记录你本轮的想法：“${message}”。\n\n我们会继续围绕ECE329课上所学的“${evidence.title}”展开。${prompt}`,
    current_stage: STAGES[state.stageIndex][0],
    interaction_state: state.mode,
    knowledge_references: [evidence],
    quick_actions: state.stageIndex === 0 ? evidence.options : [guidedAdvanceLabel(state.stageIndex)],
    warnings: [],
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
    /(接入|调用|连接|控制|转发|发布|上传).{0,40}(agent|智能体|助手|机器人|bot)/i,
    /(agent|智能体|助手|机器人|bot).{0,40}(接入|调用|连接|控制|转发|发布|上传).{0,40}(网站|平台|应用|服务|插件|频道|论坛|直播)/i,
    /(网站|平台|应用|服务|插件|频道|论坛|直播).{0,32}(强制|控制|改写|指定).{0,24}(输出|回答|翻译|内容)/i,
  ];
  if (unreasonablePatterns.some((pattern) => pattern.test(normalized))) {
    return "UNREASONABLE_REQUEST";
  }
  if (isDemoNoDirectionRequest(normalized)) return "COURSE_CONTENT";
  return directEvidence ? "COURSE_CONTENT" : "OUT_OF_SCOPE";
}

function detectDemoEmvrIntent(text) {
  const normalized = text.trim().toLocaleLowerCase();
  const negative = /(?:不要|不需要|不用|退出|取消|关闭|移除).{0,12}\bemvr\b|\bemvr\b.{0,12}(?:不要|不需要|不用|退出|取消|关闭|移除)|\b(?:do\s*not|don't|without|disable|leave|exit)\b.{0,20}\bemvr\b/i;
  if (negative.test(normalized)) return false;
  if (!/\bemvr\b/i.test(normalized)) return null;
  const positive = /(?:放入|使用|采用|切换|进入|启用|按照|通过|需要).{0,16}\bemvr\b|\bemvr\b.{0,16}(?:工作流|模式|设计|实验|完善)|\b(?:use|enable|enter|switch\s+to|with)\b.{0,20}\bemvr\b/i;
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
  const ordinalToken = "(\\d+|[一二三四五六七八九十]{1,3})";
  const patterns = [
    new RegExp(`第\\s*${ordinalToken}\\s*(?:个|项|类|条|种|方向|例子)`, "i"),
    new RegExp(`第\\s*${ordinalToken}\\s*$`, "i"),
    new RegExp(`(?:选|选择|研究|想要|考虑)\\s*(?:第\\s*)?${ordinalToken}\\s*(?:个|项|类|条|种|方向|例子)`, "i"),
    new RegExp(`(?:选|选择)\\s*(?:第\\s*)?${ordinalToken}\\s*$`, "i"),
    new RegExp(`(?:上面|刚才|之前).{0,6}${ordinalToken}\\s*(?:个|项|类|条|种|方向|例子)`, "i"),
  ];
  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (!match) continue;
    const ordinal = parseDemoOrdinal(match[1]);
    const index = ordinal === null ? -1 : ordinal - 1;
    return index >= 0 && index < normalizedOptions.length ? normalizedOptions[index] : null;
  }
  return null;
}

function parseDemoOrdinal(raw) {
  if (/^\d+$/.test(raw)) {
    const value = Number(raw);
    return value > 0 ? value : null;
  }
  const digits = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (raw === "十") return 10;
  if (raw.includes("十")) {
    const [left, right] = raw.split("十", 2);
    if ((left && !digits[left]) || (right && !digits[right])) return null;
    return (left ? digits[left] : 1) * 10 + (right ? digits[right] : 0);
  }
  return digits[raw] || null;
}

function resolveDemoSceneCombination(text, options) {
  if (!Array.isArray(options) || !options.length) return [];
  const labels = [...text.matchAll(/图景\s*([A-Z])/gi)]
    .map((match) => match[1].toUpperCase());
  const indexes = [...new Set(labels.map((label) => label.charCodeAt(0) - 65))];
  return indexes
    .filter((index) => index >= 0 && index < options.length)
    .map((index) => String(options[index]));
}

function inferDemoStandardComparisons(text) {
  const candidate = DEMO_BASELINE_COMPARISONS.find((comparison) => (
    comparison.triggerGroups.every((group) => group.some((term) => text.includes(term)))
  ));
  if (!candidate) return [];
  return [{
    comparison_id: candidate.comparison_id,
    cases: [...candidate.cases],
    recommended_cases: [...candidate.cases],
    case_aliases: Object.fromEntries(
      Object.entries(candidate.case_aliases || {}).map(([caseName, aliases]) => (
        [caseName, [...aliases]]
      )),
    ),
    role: "PROPOSED_BASELINE_COMPARISON",
    adoption_status: "PENDING",
    reason: candidate.reason,
    course_concept_ids: [...candidate.course_concept_ids],
    proposal_source: "COURSE_CATALOG",
  }];
}

function updateDemoStandardComparisonDecisions(text, comparisons, controlTurn = false) {
  const rejectsBundle = /(?:不采用|不保留|不考虑|不需要|无需|不用|不要|取消)(?:这组|这个|整组|全部)?(?:基本|标准)?(?:case|情况|情形|对照|比较)|不需要.{0,6}(?:分类|分情况)讨论|(?:这些|上述|所有|全部).{0,4}都不要/i.test(text);
  const acceptsBundle = /(?:接受|采纳|同意|保留|采用|恢复).{0,8}(?:这组|这个|整组|全部)?(?:基本|标准)?(?:case|情况|情形|对照|比较)|(?:全部|所有|这些|上述).{0,5}(?:都要|都考虑|都保留|一起做|同时做)/i.test(text);
  return (comparisons || []).map((comparison) => {
    const item = {
      ...comparison,
      cases: [...(comparison.cases || [])],
      recommended_cases: [...(comparison.recommended_cases || comparison.cases || [])],
    };
    const recommended = item.recommended_cases || item.cases || [];
    const aliases = item.case_aliases || {};
    const labelsFor = (caseName) => [caseName, ...(aliases[caseName] || [])];
    const mentioned = recommended.filter((caseName) => (
      labelsFor(caseName).some((label) => label && text.includes(label))
    ));
    const onlyInstruction = /(?:只|仅).{0,8}(?:保留|采用|考虑|研究|观察|比较|看|做)/.test(text);
    const removed = [];
    const restored = [];
    const replacements = [];
    recommended.forEach((caseName) => {
      labelsFor(caseName).some((label) => {
        const escaped = escapeRegularExpression(label);
        const removePattern = new RegExp(`(?:不采用|不保留|不考虑|不要|不用|排除|去掉|删除|移除)[^，,。；;！？?]{0,3}${escaped}|${escaped}[^，,。；;！？?]{0,3}(?:不采用|不保留|不考虑|不要|不用|排除|去掉|删除|移除)`, "i");
        const restorePattern = new RegExp(`(?:加入|加回|恢复|重新采用|重新保留|也保留)[^，,。；;！？?]{0,3}${escaped}|${escaped}[^，,。；;！？?]{0,3}(?:加入|加回|恢复|重新采用|重新保留)`, "i");
        if (removePattern.test(text)) {
          removed.push(caseName);
          return true;
        }
        if (restorePattern.test(text)) {
          restored.push(caseName);
          return true;
        }
        return false;
      });
    });
    recommended.forEach((oldCase) => {
      recommended.forEach((newCase) => {
        if (oldCase === newCase) return;
        labelsFor(oldCase).forEach((oldLabel) => {
          labelsFor(newCase).forEach((newLabel) => {
            const pattern = new RegExp(`(?:把)?${escapeRegularExpression(oldLabel)}[^，,。；;！？?]{0,4}(?:换成|替换为|改成)${escapeRegularExpression(newLabel)}`, "i");
            if (pattern.test(text)) replacements.push([oldCase, newCase]);
          });
        });
      });
    });
    const onlyCases = mentioned.filter((caseName) => !removed.includes(caseName));
    const mentionsAllAsGroup = /(?:都|一起|同时).{0,5}(?:要|考虑|保留|采用|研究|观察|比较|看|做)?/.test(text)
      && new Set(mentioned).size === new Set(recommended).size;
    if (rejectsBundle) {
      item.cases = [];
      item.adoption_status = "REJECTED";
    } else if (replacements.length) {
      replacements.forEach(([oldCase, newCase]) => {
        item.cases = item.cases.filter((caseName) => caseName !== oldCase);
        if (!item.cases.includes(newCase)) item.cases.push(newCase);
      });
      item.adoption_status = item.cases.length === recommended.length
        ? "ACCEPTED"
        : "MODIFIED";
    } else if (onlyInstruction && onlyCases.length) {
      item.cases = [...new Set(onlyCases)];
      item.adoption_status = item.cases.length === recommended.length
        ? "ACCEPTED"
        : "MODIFIED";
    } else if (removed.length) {
      item.cases = item.cases.filter((caseName) => !removed.includes(caseName));
      item.adoption_status = item.cases.length ? "MODIFIED" : "REJECTED";
    } else if (restored.length) {
      item.cases = [...new Set([...item.cases, ...restored])];
      item.adoption_status = item.cases.length === recommended.length
        ? "ACCEPTED"
        : "MODIFIED";
    } else if (acceptsBundle || mentionsAllAsGroup || (controlTurn && item.adoption_status === "PENDING")) {
      item.cases = [...recommended];
      item.adoption_status = "ACCEPTED";
    }
    return item;
  });
}

function escapeRegularExpression(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function formatDemoStandardComparisons(comparisons) {
  return (comparisons || []).map((comparison) => {
    const status = comparison.adoption_status || "PENDING";
    const cases = comparison.cases || [];
    const recommended = comparison.recommended_cases || cases;
    if (status === "REJECTED") return "已按你的决定不采用这组默认对照。";
    if (status === "MODIFIED") return cases.length
      ? `按你的决定，基本情形只保留${cases.join("与")}。`
      : "已按你的决定移除这组默认对照。";
    if (status === "ACCEPTED") return `已采纳${cases.join("与")}作为一组基本对照。`;
    return recommended.length
      ? `建议默认把${recommended.join("与")}作为一组基本对照；确认当前概括即表示采纳，也可以直接指出要删改。`
      : "";
  }).join("");
}

function buildDemoExperimentOutlineSeed(corePhenomenon, selectedRelations, comparisons) {
  const activeCases = (comparisons || []).flatMap((comparison) => (
    comparison.adoption_status === "REJECTED" ? [] : (comparison.cases || [])
  ));
  return {
    status: "DRAFT",
    core_phenomenon: corePhenomenon,
    course_relationships: [...selectedRelations],
    baseline_comparison_cases: activeCases,
    observation_focus: `观察“${corePhenomenon}”在不同对象、边界或基本情形下如何变化`,
    next_refinement_points: [
      "课程映射",
      "学习目标",
      "研究问题",
      "理论依据",
      "假设与预期趋势",
      "概念实验结构",
    ],
  };
}

function formatDemoExperimentOutlineSeed(outline) {
  const cases = outline.baseline_comparison_cases.length
    ? outline.baseline_comparison_cases.join("、")
    : "暂未加入默认对照，可在后续按需要补充";
  return [
    "实验大纲雏形",
    `核心现象：${outline.core_phenomenon}`,
    `课程内物理联系：${outline.course_relationships.join("；")}`,
    `基本观察图景：${outline.observation_focus}`,
    `建议保留的基本情形：${cases}`,
    `下一步细化：${outline.next_refinement_points.join(" → ")}`,
  ].join("\n");
}

function buildDemoIdeaDevelopmentStatus(outline, ideaText) {
  const definitions = [
    ["direction_outline", "实验现象与大纲雏形", true, outline.core_phenomenon],
    ["course_mapping", "课程映射", true, outline.course_relationships.join("；")],
    ["learning_objective", "学习目标", /学生|理解|掌握|解释|判断|学习目标/.test(ideaText), ideaText],
    ["research_question", "研究问题", /关系|影响|比较|变化|差异|区别/.test(ideaText), ideaText],
    ["theoretical_framework", "理论依据", true, "根据课程资料匹配的核心理论关系"],
    ["hypothesis", "假设与预期趋势", /假设|预测|预计|增大|减小|变强|变弱|趋势/.test(ideaText), ideaText],
    ["conceptual_structure", "概念实验结构", /场源|边界|导体|介质|负载|线圈|电荷|对象|对照/.test(ideaText), ideaText],
  ];
  const status = {
    mode: "DYNAMIC_COMPLETENESS",
    facets: definitions.map(([facetId, title, isClear, evidence]) => ({
      facet_id: facetId,
      title,
      status: isClear ? "CLEAR" : "MISSING",
      evidence: isClear ? evidence : "",
      source: isClear ? "EXISTING_IDEA" : null,
    })),
    active_facet_id: null,
    completed_facet_ids: [],
    missing_facet_ids: [],
    last_clarified_facet_ids: [],
    complete: false,
  };
  refreshDemoIdeaDevelopmentStatus(status);
  return status;
}

function updateDemoIdeaDevelopmentStatus(status, message) {
  const clarified = [];
  const active = status.facets.find((facet) => facet.facet_id === status.active_facet_id);
  if (active && isSubstantiveDemoFacetAnswer(active.facet_id, message)) {
    active.status = "CLEAR";
    active.evidence = message.trim();
    active.source = "STUDENT";
    clarified.push(active.facet_id);
  }
  const patterns = {
    learning_objective: /学生|理解|掌握|解释|判断|学习目标/,
    research_question: /关系|影响|比较|变化|差异|区别/,
    theoretical_framework: /理论|公式|方程|定律|边界条件/,
    hypothesis: /假设|预测|预计|增大|减小|变强|变弱|趋势/,
    conceptual_structure: /场源|边界|导体|介质|负载|线圈|电荷|对象|对照/,
  };
  Object.entries(patterns).forEach(([facetId, pattern]) => {
    const facet = status.facets.find((item) => item.facet_id === facetId);
    if (facet && pattern.test(message)) {
      if (facet.status !== "CLEAR") clarified.push(facetId);
      facet.status = "CLEAR";
      facet.evidence = message.trim();
      facet.source = "STUDENT";
    }
  });
  status.last_clarified_facet_ids = [...new Set(clarified)];
  refreshDemoIdeaDevelopmentStatus(status);
}

function isSubstantiveDemoFacetAnswer(facetId, message) {
  const text = message.trim();
  if (text.length < 6 || /^(继续|下一步|好的|可以|没问题|不知道|不确定|没想好|暂时没有|还不清楚)[。！!？?]*$/.test(text)) {
    return false;
  }
  const patterns = {
    learning_objective: /理解|解释|判断|比较|计算|分析|掌握|能够|学会/,
    research_question: /比较|影响|关系|差异|区别|变化|改变|如何|怎样|是否|随.+(?:变|增|减)|与|和|、/,
    hypothesis: /预计|预期|预测|假设|会|将|增大|减小|增强|减弱|移动|趋于|因为|由于|所以/,
    conceptual_structure: /包含|包括|组成|场源|激励|对象|边界|导体|介质|负载|线圈|电荷|对照|参照/,
    course_mapping: /ECE329|课程|静电场|磁场|电磁波|传输线|边界条件/i,
    theoretical_framework: /理论|公式|方程|定律|边界条件|高斯|法拉第|安培|麦克斯韦|反射系数/,
    direction_outline: /研究|探究|观察|比较|现象|关系|变化/,
  };
  return Boolean(patterns[facetId]?.test(text));
}

function refreshDemoIdeaDevelopmentStatus(status) {
  status.completed_facet_ids = status.facets
    .filter((facet) => facet.status === "CLEAR")
    .map((facet) => facet.facet_id);
  status.missing_facet_ids = status.facets
    .filter((facet) => facet.status !== "CLEAR")
    .map((facet) => facet.facet_id);
  const priority = [
    "research_question",
    "learning_objective",
    "hypothesis",
    "conceptual_structure",
    "course_mapping",
    "theoretical_framework",
  ];
  status.active_facet_id = priority.find((facetId) => status.missing_facet_ids.includes(facetId))
    || status.missing_facet_ids[0]
    || null;
  status.complete = status.missing_facet_ids.length === 0;
}

function demoIdeaDevelopmentTask(status) {
  if (status.complete) {
    return "必要内容已经齐全。请整体检查；若准确，可确认想法完善并进入变量与条件，若有遗漏请直接补充。";
  }
  const tasks = {
    learning_objective: "你希望自己完成这个实验后能够解释、判断或比较什么？请描述一种最重要的能力。",
    research_question: "请把当前想法压缩成一个可回答的问题：你想比较什么条件，并观察哪种现象怎样改变？",
    hypothesis: "根据当前理论依据，你预计关键条件改变时，观察现象会朝什么方向变化？请说明物理理由。",
    conceptual_structure: "这个想法至少需要哪些对象、边界或激励条件？这里只描述组成部分，不需要写实现步骤。",
  };
  return tasks[status.active_facet_id] || "请补充当前实验想法中仍未明确的关键内容。";
}

function demoStudentFacingNextTurn(status, firstReview = false) {
  if (status.complete) {
    return "现在，这个实验想法中的研究对象、课程依据、学习目标和预期现象已经能够相互对应。请整体看一遍；如果与自己的想法一致，直接告诉我进入“变量与条件”。如果还有想调整的地方，也可以直接说明。";
  }
  const active = status.facets.find((facet) => facet.facet_id === status.active_facet_id);
  const title = active?.title || "下一部分";
  const prefix = firstReview
    ? "这个方向已经形成了可以继续发展的实验雏形。"
    : "我们继续沿着同一个实验方向往下完善。";
  return `${prefix} 接下来先把“${title}”说清楚：${demoIdeaDevelopmentTask(status)}`;
}

function demoStudentFacingRetry(status) {
  const feedback = {
    learning_objective: "我理解了你补充的现象，但这里还需要更明确地说出你完成实验后能够解释、判断或比较什么。",
    research_question: "我保留了你刚才的补充，但研究问题还需要同时出现要比较的条件和准备观察的变化。",
    hypothesis: "你已经描述了可能看到的现象；要把它变成实验预期，还需要说明这种变化背后的物理理由。",
    conceptual_structure: "我理解了你的补充，但还需要说明这个想法中有哪些对象、边界或激励共同构成比较。",
  };
  return feedback[status.active_facet_id]
    || "我保留了你刚才的补充，但还需要把它与当前实验想法的物理关系说得更具体。";
}

function demoIdeaAcknowledgement(message, clarifiedFacetIds, status) {
  const excerpt = String(message || "").trim().replace(/\s+/g, " ");
  const titles = clarifiedFacetIds
    .map((facetId) => status.facets.find((facet) => facet.facet_id === facetId)?.title)
    .filter(Boolean);
  if (titles.length === 1 && titles[0] === "学习目标") {
    return `这个学习目标表达得很清楚：“${excerpt}”。`;
  }
  if (titles.length === 1 && titles[0] === "研究问题") {
    return `这个研究问题已经很具体：“${excerpt}”。`;
  }
  if (titles.length === 1 && titles[0] === "假设与预期趋势") {
    return `你的预测已经同时给出了现象和判断：“${excerpt}”。`;
  }
  return `你的回答很清楚：“${excerpt}”。这已经把${titles.map((title) => `“${title}”`).join("、")}说明得更具体。`;
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
  if (response.stage_payload?.experiment_outline_seed) {
    state.experimentOutlineSeed = response.stage_payload.experiment_outline_seed;
  }
  if (response.stage_payload?.idea_development_status) {
    state.ideaDevelopmentStatus = response.stage_payload.idea_development_status;
  }
  if (state.stageIndex === 0) {
    const inputCategory = response.stage_payload?.input_category;
    if (response.stage_payload?.brainstorm_phase) {
      state.stageOnePhase = response.stage_payload.brainstorm_phase;
    }
    if (Array.isArray(response.stage_payload?.standard_comparisons)) {
      state.stageOneStandardComparisons = response.stage_payload.standard_comparisons
        .map((comparison) => ({
          ...comparison,
          cases: [...(comparison.cases || [])],
          recommended_cases: [
            ...(comparison.recommended_cases || comparison.cases || []),
          ],
        }));
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
  const handledStageId = response.handled_stage || response.current_stage || stageId;
  const handledIndex = STAGES.findIndex(([id]) => id === handledStageId);
  const displayIndex = handledIndex >= 0 ? handledIndex : state.stageIndex;
  const tags = [
    displayIndex < IDEA_DEVELOPMENT_STAGE_IDS.length
      ? "阶段 1 · 想法完善"
      : `阶段 ${workflowGroupIndex(displayIndex) + 1}`,
    ...(response.warnings?.length ? ["含提示"] : []),
  ];
  addMessage("assistant", text, tags, {
    meta: response._runtime_source === "api" ? "ECE329 Agent" : "页面演示",
  });

  if (!state.notes.some((note) => note.includes(userMessage.slice(0, 40)))) {
    state.notes.push(`你的输入：${userMessage.slice(0, 90)}`);
  }

  if (response.visualization) {
    state.visualization = response.visualization;
    dom.chartDescription.textContent = response.visualization.disclaimer || "该图表示理论预测，不是实际测量数据。";
  }
}

function composeAssistantText(response) {
  const base = response.assistant_message || response.message || "Agent已处理当前阶段。";
  const parts = [base];
  const shouldShowStudentTask = state.stageIndex !== 0;
  const studentTask = shouldShowStudentTask && typeof response.student_task === "string"
    ? response.student_task.trim()
    : "";
  if (studentTask && !base.includes(studentTask)) parts.push(studentTask);
  const warnings = Array.isArray(response.warnings)
    ? response.warnings.filter((item) => typeof item === "string" && item.trim())
    : [];
  if (warnings.length) parts.push(`提示：${warnings.join("；")}`);
  if (response.completion_error && state.stageIndex !== 0) {
    parts.push(`尚未推进：${response.completion_error}`);
  }
  return parts.join("\n\n");
}

function deriveQuickActions(response) {
  if (response.quick_actions) return response.quick_actions;
  if (response.workflow_status === "complete" || response.status === "complete") return [];
  if (state.mode === "EMVR_DIRECT") return ["继续完善下一阶段"];
  if (response.stage_payload?.awaiting_student_description === true) return [];

  if (response.current_stage && response.handled_stage && response.current_stage !== response.handled_stage) {
    return [`继续${currentWorkspaceTitle(state.stageIndex)}`];
  }

  if (state.stageIndex === 0) {
    const development = response.stage_payload?.idea_development_status;
    if (development) {
      return development.complete ? ["确认想法完善并进入变量与条件"] : [];
    }
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
    return [];
  }
  if (state.stageIndex === STAGES.length - 1) {
    return String(state.pendingSummary || "").trim().length >= 20
      && (state.summarySections || []).filter((section) => String(section).trim().length >= 10).length >= 2
      ? ["确认完成总结"]
      : [];
  }
  return [guidedAdvanceLabel(state.stageIndex)];
}

function guidedAdvanceLabel(stageIndex) {
  return stageIndex === 0
    ? "确认想法完善并进入变量与条件"
    : "确认本阶段并进入下一阶段";
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
