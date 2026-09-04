"use strict";

const CONFIG = window.ECE329_CONFIG || { API_BASE_URL: "", REQUEST_TIMEOUT_MS: 180000 };
const STORAGE_KEY = "ece329-lab-studio-session-v1";
const DESIGN_TOKEN_KEY = "ece329-design-access-token-v1";
const DESIGN_RESUME_KEY = "ece329-design-resume-key-v1";
const ACCESS_CODE_KEY = "ece329-course-access-code-v1";
const LEGACY_INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们先从讲义中的概念出发探索想法，不急着写完整方案。\n\n请描述一个你感兴趣的电磁现象，或者告诉我你还没有具体方向。";
const PREVIOUS_INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们会从ECE329课上所学的电磁场、电磁波和传输线概念出发，一起探索实验想法，不急着写完整方案。\n\n你可以描述一个感兴趣的现象，例如静电场与材料边界、磁场与电磁感应、电磁波的偏振与反射，或传输线中的反射与驻波。如果暂时没有方向，也可以直接告诉我。";
const INITIAL_GREETING = "欢迎来到 ECE329 Lab Studio。我们先了解你的想法，再一起把它发展成清晰的实验方向，不急着写完整方案。\n\n请先用自己的话说说：你目前对哪个ECE329现象或概念有兴趣？如果还没有具体思路，也可以直接告诉我，我会先带你浏览课上所学内容的大致方向。";

class ApiError extends Error {
  constructor(message, status = 0, code = "request_failed", details = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details && typeof details === "object" ? details : {};
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
  "对照前面提出的研究问题和预期，哪一种图或场分布最容易看出关键变化？",
  "如果显示结果符合或偏离前面的预期，各自能说明什么？",
  "对照最初的学习目标，现在的设计能否支撑它，又受哪种理想化条件限制？",
  "请把研究对象、主要比较和ECE329课程关系用一小段话串起来。",
];

const DEMO_GUIDED_STAGE_ENTRY_QUESTIONS = Object.freeze({
  VARIABLES_AND_CONDITIONS: "先不急着列完整变量表。按照你的理解，这个实验中哪些量应该主动改变、哪些现象需要观察，又有哪些条件应该保持不变？可以先说你认为最重要的部分。",
  CONCEPTUAL_PROCEDURE: "先不急着写标准流程。你认为在这个实验中，从建立比较基准到改变条件、观察现象和比较结果，需要经历哪些关键环节？请先按自己的思路描述。",
  EXPECTED_DATA_VISUALIZATION: "前面已经有研究问题和预期趋势了。你觉得用一条曲线、几幅场分布图，还是两种显示配合起来，最容易判断预期有没有出现？如果暂时不确定，我可以先按已有变量搭一版参考。",
  RESULT_INTERPRETATION: "回到前面提出的预期：如果显示结果与它一致，能支持哪部分解释；如果不一致，又该先检查条件、模型还是原来的判断？",
  DESIGN_VALUE_AND_LIMITATIONS: "最初的学习目标已经保留下来了，这里不用再重复。请看看现在的变量、流程和显示是否足以实现它，并指出一个最可能限制结论的理想化条件。",
  STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "请用一小段话把这个实验研究什么、比较或观察什么，以及它和ECE329哪部分内容有关串起来；不用再重复学习收获。",
});

const DEMO_GUIDED_STAGE_REFERENCE_STEPS = Object.freeze({
  VARIABLES_AND_CONDITIONS: ["列出主动改变的量", "列出准备观察或比较的现象", "固定其余会影响比较的条件"],
  CONCEPTUAL_PROCEDURE: ["建立可重复的基准条件", "逐步改变前面确定的条件", "用相同方式观察并记录", "比较各组结果并联系课程关系解释"],
  EXPECTED_DATA_VISUALIZATION: ["以前面确定的改变量作为横轴或控制量", "显示最重要的观察量", "并列保留的基础情形", "标明这是理论预测而非实测数据"],
  RESULT_INTERPRETATION: ["解释符合预期的结果", "检查偏离预期时的条件或假设", "区分模型局限与物理差异"],
  DESIGN_VALUE_AND_LIMITATIONS: ["对照阶段1的学习目标检查现有设计是否足以支撑它", "指出一个关键理想化条件", "区分设计能说明和不能推出的内容"],
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
  taskReportCard: document.querySelector("#taskReportCard"),
  taskReportStatus: document.querySelector("#taskReportStatus"),
  taskReportIdea: document.querySelector("#taskReportIdea"),
  taskReportSections: document.querySelector("#taskReportSections"),
  downloadReportButton: document.querySelector("#downloadReportButton"),
  downloadBuilderInputButton: document.querySelector("#downloadBuilderInputButton"),
  qualityReviewCard: document.querySelector("#qualityReviewCard"),
  qualityReviewStatus: document.querySelector("#qualityReviewStatus"),
  qualityCausalChain: document.querySelector("#qualityCausalChain"),
  qualityFeasibility: document.querySelector("#qualityFeasibility"),
  qualityIssueList: document.querySelector("#qualityIssueList"),
  qualityBoundaryCases: document.querySelector("#qualityBoundaryCases"),
  qualityBoundaryCasesContent: document.querySelector("#qualityBoundaryCasesContent"),
  qualityOptionComparison: document.querySelector("#qualityOptionComparison"),
  qualityOptionComparisonContent: document.querySelector("#qualityOptionComparisonContent"),
  qualityTraceability: document.querySelector("#qualityTraceability"),
  qualityTraceabilityContent: document.querySelector("#qualityTraceabilityContent"),
  viewVersionsButton: document.querySelector("#viewVersionsButton"),
  undoVersionButton: document.querySelector("#undoVersionButton"),
  versionResults: document.querySelector("#versionResults"),
  downloadGuidedSummaryButton: document.querySelector("#downloadGuidedSummaryButton"),
  toast: document.querySelector("#toast"),
};

let state = loadState();
let typingMessageId = null;
let toastTimer = null;
let connectionState = apiBase() ? "checking" : "demo";
let lastConnectionError = null;
let replayingPendingRequest = false;
let designGeneration = 0;
const activeRequestControllers = new Set();

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
    pendingUiAction: null,
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
    taskReport: null,
    reportReady: false,
    reportUrl: null,
    builderInputReady: false,
    builderInputUrl: null,
    builderHandoffStatus: null,
    qualityReview: null,
    versionControl: null,
    recentVersions: null,
    guidedExportReady: false,
    guidedExportUrl: null,
    pendingRequest: null,
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

function invalidateDesignRequests() {
  designGeneration += 1;
  for (const controller of activeRequestControllers) controller.abort();
  activeRequestControllers.clear();
  typingMessageId = null;
}

function apiBase() {
  return String(CONFIG.API_BASE_URL || "").trim().replace(/\/$/, "");
}

async function apiRequest(path, options = {}) {
  const controller = new AbortController();
  activeRequestControllers.add(controller);
  const timeoutMs = Number(CONFIG.REQUEST_TIMEOUT_MS) || 180000;
  let timedOut = false;
  const timeout = window.setTimeout(
    () => {
      timedOut = true;
      controller.abort();
    },
    timeoutMs,
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
        payload,
      );
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error?.name === "AbortError") {
      throw new ApiError(
        timedOut ? "Browser request timeout" : "Request was aborted",
        0,
        timedOut ? "client_timeout" : "request_aborted",
        { timeout_ms: timeoutMs },
      );
    }
    if (error instanceof TypeError) {
      throw new ApiError(
        "Browser could not reach the workflow backend",
        0,
        "network_error",
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    activeRequestControllers.delete(controller);
  }
}

function requestFailurePresentation(error) {
  const apiError = error instanceof ApiError ? error : null;
  const code = apiError?.code || "unknown_client_error";
  const status = Number(apiError?.status || 0);
  const diagnosticId = String(apiError?.details?.diagnostic_id || "").trim();
  const suffix = diagnosticId ? `（诊断编号：${diagnosticId}）` : "";
  const phase = String(apiError?.details?.phase || "").trim();
  const phaseLabel = {
    intent_analysis: "理解本轮意图",
    response_generation: "生成正式回复",
  }[phase] || "";
  const phaseSuffix = phaseLabel ? `（发生在：${phaseLabel}）` : "";
  const timeoutSeconds = Math.round(Number(apiError?.details?.timeout_ms || 0) / 1000);
  const presentations = {
    client_timeout: {
      message: `本轮处理已超过浏览器等待时间${timeoutSeconds ? `（约${timeoutSeconds}秒）` : ""}，页面已停止等待。后端可能仍在处理；请先稍等片刻，再重新发送刚才的回答。`,
      tag: "浏览器等待超时",
      badge: "课程服务响应较慢",
      notice: "这不是课程内容错误；通常是本轮模型调用链较长或服务响应较慢。",
      toast: "浏览器等待超时，设计已保留",
    },
    network_error: {
      message: "浏览器没有连接到课程服务。请检查当前网络、后端地址和跨域配置，然后重新发送刚才的回答。",
      tag: "网络连接失败",
      badge: "无法连接课程服务",
      notice: "请求没有获得 HTTP 响应，可能与网络、DNS、后端地址或 CORS 有关。",
      toast: "浏览器未连接到后端",
    },
    request_aborted: {
      message: "本轮请求已被页面中止，当前设计没有改变。请重新发送刚才的回答。",
      tag: "请求已中止",
      badge: "请求已中止",
      notice: "页面切换设计或取消了尚未完成的请求。",
      toast: "请求已中止",
    },
    model_timeout: {
      message: `课程服务已收到请求，但模型没有在后端规定时间内完成本轮处理${phaseSuffix}。当前设计已保留，可以直接重试。${suffix}`,
      tag: "模型响应超时",
      badge: "模型响应超时",
      notice: "后端连接正常；失败发生在模型等待阶段，可在 Render 日志中按诊断编号定位。",
      toast: "模型响应超时",
    },
    model_rate_limited: {
      message: `课程服务已收到请求，但模型接口当前限流。请稍等后重新发送，当前设计和进度已保留。${suffix}`,
      tag: "模型接口限流",
      badge: "模型接口繁忙",
      notice: "后端连接正常；上游模型接口暂时限制了请求频率。",
      toast: "模型接口限流",
    },
    model_output_invalid: {
      message: `模型已经返回内容，但返回结果没有通过工作流的结构或课程范围检查${phaseSuffix}，因此没有写入设计。请重新发送本轮回答；若反复出现，请用诊断编号检查 Render 日志。${suffix}`,
      tag: "模型输出未通过校验",
      badge: "回答格式校验失败",
      notice: "这通常与结构化 JSON、字段约束或输出截断有关，不代表浏览器断网。",
      toast: "模型输出未通过校验",
    },
    model_request_rejected: {
      message: `后端请求模型时被上游接口拒绝。当前设计未改变；请检查模型名称、参数和 API 配置。${suffix}`,
      tag: "模型请求被拒绝",
      badge: "模型请求配置异常",
      notice: "后端连接正常，但上游模型接口未接受本次请求。",
      toast: "模型请求被拒绝",
    },
    model_configuration_error: {
      message: `课程服务的模型配置不完整或无效，本轮无法继续。请由管理员检查 Render 环境变量。${suffix}`,
      tag: "后端配置错误",
      badge: "模型尚未正确配置",
      notice: "请检查 API 密钥、模型名称、推理等级和输出预算等后端配置。",
      toast: "模型配置错误",
    },
    model_connection_error: {
      message: `课程后端无法连接模型服务。当前设计已保留，请稍后重试。${suffix}`,
      tag: "后端到模型连接失败",
      badge: "模型连接失败",
      notice: "浏览器与课程后端已连接；故障发生在后端访问模型服务时。",
      toast: "后端无法连接模型服务",
    },
    model_upstream_error: {
      message: `模型服务返回了临时服务器错误。当前设计已保留，请稍后重试。${suffix}`,
      tag: "模型服务异常",
      badge: "模型服务暂时异常",
      notice: "课程后端已收到请求，上游模型服务返回了 5xx 错误。",
      toast: "上游模型服务异常",
    },
    invalid_request: {
      message: `课程服务收到了请求，但本轮请求格式或工作流状态没有通过检查。当前设计未改变。${suffix}`,
      tag: "请求校验失败",
      badge: "本轮请求无效",
      notice: "这不是网络问题；请刷新当前设计状态后重试。",
      toast: "请求未通过工作流校验",
    },
    invalid_json: {
      message: `课程服务无法解析本轮请求的数据格式。当前设计未改变。${suffix}`,
      tag: "请求格式错误",
      badge: "请求格式错误",
      notice: "后端已收到请求，但请求正文不是有效的数据格式。",
      toast: "请求格式错误",
    },
    request_too_large: {
      message: `本轮输入超过课程服务允许的长度，请精简后重新发送。当前设计未改变。${suffix}`,
      tag: "输入过长",
      badge: "输入超过限制",
      notice: "后端已收到请求；请缩短本轮输入，而不需要重新开始设计。",
      toast: "本轮输入过长",
    },
    storage_unavailable: {
      message: `课程服务暂时无法读取或保存设计记录。为避免内容丢失，本轮没有继续处理。${suffix}`,
      tag: "设计存储异常",
      badge: "设计存储暂时不可用",
      notice: "请稍后重试；管理员可用诊断编号检查 Render 存储日志。",
      toast: "设计存储异常",
    },
    internal_error: {
      message: `课程服务内部出现未预期错误，本轮设计没有更新。请记录诊断编号并联系管理员。${suffix}`,
      tag: "后端内部错误",
      badge: "课程服务内部错误",
      notice: "后端已收到请求，但工作流执行失败；可在 Render 日志中按诊断编号定位。",
      toast: "后端内部错误",
    },
    origin_not_allowed: {
      message: "课程后端拒绝了当前网页来源。请检查 Render 中允许的 GitHub Pages 域名配置。",
      tag: "跨域配置错误",
      badge: "网页来源未获授权",
      notice: "浏览器已连接后端，但当前 Origin 不在后端允许列表中。",
      toast: "请检查后端 CORS 配置",
    },
    route_not_found: {
      message: "课程服务中没有找到当前接口。请检查网页配置的后端地址以及前后端版本是否一致。",
      tag: "接口地址错误",
      badge: "前后端接口不匹配",
      notice: "后端可以访问，但请求路径不存在。",
      toast: "接口地址不存在",
    },
    workflow_error: {
      message: `工作流拒绝了本轮状态操作，当前设计没有改变。请刷新设计状态后重试。${suffix}`,
      tag: "工作流状态冲突",
      badge: "工作流状态异常",
      notice: "后端已收到请求，但该操作与当前设计状态不兼容。",
      toast: "工作流状态异常",
    },
    backend_unavailable: {
      message: "课程服务目前没有通过就绪检查。请稍后重试，并查看 Render 的 /ready 与服务日志。",
      tag: "后端尚未就绪",
      badge: "课程服务尚未就绪",
      notice: "后端地址已配置，但服务当前未达到可处理请求的状态。",
      toast: "课程服务尚未就绪",
    },
  };
  if (presentations[code]) return presentations[code];
  if (status === 504) return presentations.model_timeout;
  if (status === 502 || status === 503) return {
    message: `课程后端已收到请求，但依赖服务暂时不可用。当前设计已保留，请稍后重试。${suffix}`,
    tag: `服务异常 · HTTP ${status}`,
    badge: "依赖服务暂时异常",
    notice: "这不是浏览器断网；请结合 HTTP 状态和 Render 日志进一步定位。",
    toast: `服务异常（HTTP ${status}）`,
  };
  return {
    message: `本轮请求没有完成，当前设计已保留。错误类型：${code}${status ? `，HTTP ${status}` : ""}。${suffix}`,
    tag: "请求失败",
    badge: "本轮请求未完成",
    notice: "请记录错误类型和诊断编号后查看浏览器 Network 与 Render 日志。",
    toast: "本轮请求未完成",
  };
}

async function checkConnection() {
  if (!apiBase()) {
    setConnectionState("demo", "本地示例 · 课程服务未连接");
    lastConnectionError = null;
    dom.offlineNotice.hidden = false;
    return;
  }
  try {
    await apiRequest("/ready", { method: "GET" });
    lastConnectionError = null;
    setConnectionState("online", "课程服务已连接");
    dom.offlineNotice.hidden = true;
  } catch (error) {
    lastConnectionError = error;
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
  renderTaskReport();
  renderQualityReview();
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
    const { label, optionId, actionType } = normalizeQuickAction(action);
    if (!label) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-action";
    button.textContent = label;
    button.addEventListener("click", () => {
      if (actionType === "RETRY_PENDING_REQUEST") {
        retryPendingRequest();
        return;
      }
      dom.chatInput.value = label;
      state.pendingOptionId = optionId;
      state.pendingUiAction = actionType;
      autoGrowInput();
      dom.chatInput.focus();
    });
    dom.quickActions.append(button);
  });
}

function normalizeQuickAction(action) {
  if (typeof action === "string") return { label: action, optionId: null, actionType: null };
  if (!action || typeof action !== "object") {
    return { label: "", optionId: null, actionType: null };
  }
  return {
    label: String(action.label || action.focus || action.direction || "").trim(),
    optionId: typeof action.option_id === "string" ? action.option_id : null,
    actionType: ["ADVANCE_STAGE", "RETRY_PENDING_REQUEST"].includes(action.action)
      ? action.action
      : null,
  };
}

function retryPendingRequest() {
  const pending = state.pendingRequest;
  if (!pending || dom.sendButton.disabled) return;
  replayingPendingRequest = true;
  dom.chatInput.value = pending.message || "";
  state.pendingUiAction = pending.uiAction || null;
  state.pendingOptionId = pending.optionId || null;
  autoGrowInput();
  dom.chatForm.requestSubmit();
}

function pendingRequestRetryAction() {
  return { label: "重新发送刚才的回答", action: "RETRY_PENDING_REQUEST" };
}

function renderEvidence() {
  dom.evidenceContent.replaceChildren();
  if (!state.evidence) {
    const empty = document.createElement("p");
    empty.className = "empty-copy";
    empty.textContent = "输入想法后，这里会显示与当前实验相关的课程主题、概念和公式依据。";
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
    detail.textContent = formatConcepts(item.concepts) || item.expression || "相关依据来自已核对的ECE329课程资料。";
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

function renderTaskReport() {
  const emvr = state.mode === "EMVR_DIRECT";
  const report = state.taskReport;
  dom.taskReportCard.hidden = !emvr || !report;
  if (!emvr || !report) return;

  const handoff = state.builderHandoffStatus;
  dom.taskReportStatus.textContent = state.reportReady
    ? "设计与Builder交接已完整"
    : handoff && Number.isFinite(handoff.completed) && Number.isFinite(handoff.required)
      ? `Builder交接 ${handoff.completed}/${handoff.required}`
      : "整理中";
  dom.taskReportIdea.textContent = report.idea
    ? `当前设计：${report.idea}`
    : "当前设计会随着对话逐步补全。";
  dom.taskReportSections.replaceChildren();
  (report.sections || []).forEach((section) => {
    const details = document.createElement("details");
    details.className = "task-report-section";
    if (section.stage_id === STAGES[state.stageIndex]?.[0] || state.reportReady) {
      details.open = true;
    }
    const summary = document.createElement("summary");
    summary.textContent = section.title || "设计内容";
    const list = document.createElement("dl");
    (section.items || []).forEach((item) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = item.label || "内容";
      description.textContent = item.value || "";
      row.append(term, description);
      list.append(row);
    });
    details.append(summary, list);
    dom.taskReportSections.append(details);
  });
  dom.downloadReportButton.hidden = !state.reportReady || !state.reportUrl;
  dom.downloadBuilderInputButton.hidden = !state.builderInputReady || !state.builderInputUrl;
}

function renderQualityReview() {
  const review = state.qualityReview;
  const hasReview = review && typeof review === "object";
  const hasVersionData = state.versionControl || state.recentVersions;
  dom.qualityReviewCard.hidden = !hasReview && !hasVersionData && !state.guidedExportReady;
  if (dom.qualityReviewCard.hidden) return;

  const issues = Array.isArray(review?.issues) ? review.issues : [];
  dom.qualityReviewStatus.textContent = review?.status === "READY" ? "衔接完整" : "持续检查";
  const causal = review?.causal_chain || {};
  const causalParts = [
    causal.cause ? `改变：${causal.cause}` : "",
    causal.response ? `观察：${causal.response}` : "",
    causal.mechanism ? `依据：${causal.mechanism}` : "",
    causal.comparison ? `比较：${causal.comparison}` : "",
  ].filter(Boolean);
  dom.qualityCausalChain.replaceChildren();
  if (causalParts.length) {
    const title = document.createElement("strong");
    title.textContent = "当前因果链";
    const text = document.createElement("span");
    text.textContent = causalParts.join(" → ");
    dom.qualityCausalChain.append(title, text);
  }

  const feasibility = review?.feasibility || {};
  const feasibilityChecks = [
    ["自变量可调整", feasibility.independent_variable_can_change],
    ["观察量可记录", feasibility.observation_can_be_recorded],
    ["比较条件已定义", feasibility.comparison_is_defined],
    ["控制条件已定义", feasibility.controls_are_defined],
    ["流程能够检验预期", feasibility.procedure_can_test_hypothesis],
  ];
  dom.qualityFeasibility.replaceChildren();
  if (hasReview) {
    const title = document.createElement("strong");
    title.textContent = "概念可行性";
    const text = document.createElement("span");
    text.textContent = feasibilityChecks
      .map(([label, passed]) => `${passed ? "✓" : "○"} ${label}`)
      .join(" · ");
    dom.qualityFeasibility.append(title, text);
  }

  dom.qualityIssueList.replaceChildren();
  issues.forEach((issue) => {
    const item = document.createElement("li");
    if (issue.severity === "MAJOR") item.classList.add("major");
    item.textContent = [issue.finding, issue.suggestion].filter(Boolean).join(" 建议：");
    dom.qualityIssueList.append(item);
  });

  const boundaryCases = Array.isArray(review?.boundary_cases) ? review.boundary_cases : [];
  dom.qualityBoundaryCases.hidden = boundaryCases.length === 0;
  dom.qualityBoundaryCasesContent.replaceChildren();
  boundaryCases.slice(0, 6).forEach((entry) => {
    const item = document.createElement("p");
    item.textContent = `${entry.case || "边界情形"}${entry.relevance ? ` — ${entry.relevance}` : ""}`;
    dom.qualityBoundaryCasesContent.append(item);
  });

  const optionComparison = Array.isArray(review?.option_comparison) ? review.option_comparison : [];
  dom.qualityOptionComparison.hidden = optionComparison.length === 0;
  dom.qualityOptionComparisonContent.replaceChildren();
  optionComparison.slice(0, 6).forEach((option) => {
    const item = document.createElement("div");
    item.className = "quality-option-item";
    const title = document.createElement("strong");
    title.textContent = option.name || "候选方案";
    const detail = document.createElement("p");
    detail.textContent = [
      option.observability ? `可观察性：${option.observability}` : "",
      option.course_alignment ? `课程联系：${option.course_alignment}` : "",
      option.controllability ? `可控性：${option.controllability}` : "",
      option.vr_suitability ? `VR适配：${option.vr_suitability}` : "",
      option.recommendation ? `建议：${option.recommendation}` : "",
    ].filter(Boolean).join("；");
    item.append(title, detail);
    dom.qualityOptionComparisonContent.append(item);
  });

  const traceability = Array.isArray(review?.traceability) ? review.traceability : [];
  dom.qualityTraceability.hidden = traceability.length === 0;
  dom.qualityTraceabilityContent.replaceChildren();
  traceability.slice(0, 20).forEach((entry) => {
    const item = document.createElement("p");
    item.textContent = `${entry.design_field_label || "设计内容"}：${entry.course_item || ""}${entry.purpose ? ` — ${entry.purpose}` : ""}`;
    dom.qualityTraceabilityContent.append(item);
  });

  renderVersionResults();
  const canUseVersions = Boolean(apiBase() && state.designId && state.sessionKind === "api");
  dom.viewVersionsButton.disabled = !canUseVersions;
  dom.undoVersionButton.disabled = !canUseVersions;
  dom.downloadGuidedSummaryButton.hidden = !state.guidedExportReady || !state.guidedExportUrl;
}

function renderVersionResults() {
  dom.versionResults.replaceChildren();
  const result = Array.isArray(state.versionControl)
    ? state.versionControl.at(-1)
    : state.versionControl;
  const recent = result?.action === "VIEW_RECENT" ? result : state.recentVersions;
  if (recent?.action === "VIEW_RECENT" && Array.isArray(recent.versions)) {
    recent.versions.slice().reverse().forEach((version) => {
      const row = document.createElement("div");
      row.className = "version-result-item";
      const text = document.createElement("p");
      const changed = Array.isArray(version.changed_field_labels) && version.changed_field_labels.length
        ? version.changed_field_labels.join("、")
        : "设计起点";
      text.textContent = `${version.version_id} · ${version.reason || "设计修改"} · ${changed}`;
      const actions = document.createElement("div");
      actions.className = "version-result-actions";
      const compare = document.createElement("button");
      compare.type = "button";
      compare.textContent = "与当前比较";
      compare.addEventListener("click", () => sendVersionAction({
        action: "COMPARE",
        version_id: version.version_id,
      }, "比较这个版本与当前设计"));
      const restore = document.createElement("button");
      restore.type = "button";
      restore.textContent = "恢复此版本";
      restore.addEventListener("click", () => sendVersionAction({
        action: "RESTORE",
        version_id: version.version_id,
      }, `恢复设计版本 ${version.version_id}`));
      actions.append(compare, restore);
      row.append(text, actions);
      dom.versionResults.append(row);
    });
    return;
  }
  if (result?.action === "COMPARE" && Array.isArray(result.differences)) {
    result.differences.forEach((difference) => {
      const item = document.createElement("p");
      item.textContent = `${difference.label || "设计内容"}：${formatVersionValue(difference.before)} → ${formatVersionValue(difference.after)}`;
      dom.versionResults.append(item);
    });
    return;
  }
  if (result?.error) {
    const item = document.createElement("p");
    item.textContent = result.error;
    dom.versionResults.append(item);
  }
}

function formatVersionValue(value) {
  if (value === null || value === undefined || value === "") return "未明确";
  if (Array.isArray(value)) return value.map((item) => (
    typeof item === "object" ? JSON.stringify(item) : String(item)
  )).join("；");
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

async function downloadTaskReport() {
  if (!state.designId || !state.reportUrl || !apiBase()) return;
  dom.downloadReportButton.disabled = true;
  dom.downloadReportButton.textContent = "正在生成 PDF…";
  try {
    const response = await authorizedDesignDownload(state.reportUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ece329-emvr-${state.designId}.pdf`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("PDF 总结已下载");
  } catch (error) {
    console.warn("Unable to download EMVR report", error);
    showToast("PDF 下载失败，请确认课程服务已更新后重试");
  } finally {
    dom.downloadReportButton.disabled = false;
    dom.downloadReportButton.textContent = "下载学生版设计报告 PDF";
  }
}

async function downloadBuilderInput() {
  if (!state.designId || !state.builderInputUrl || !apiBase()) return;
  dom.downloadBuilderInputButton.disabled = true;
  dom.downloadBuilderInputButton.textContent = "正在生成 Builder 输入 PDF…";
  try {
    const response = await authorizedDesignDownload(state.builderInputUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ece329-emvr-builder-gate1-${state.designId}.pdf`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("Builder Gate 1 输入 PDF 已下载");
  } catch (error) {
    console.warn("Unable to download Builder Gate 1 input", error);
    showToast("Builder 输入 PDF 下载失败，请确认课程服务已更新后重试");
  } finally {
    dom.downloadBuilderInputButton.disabled = false;
    dom.downloadBuilderInputButton.textContent = "下载 Builder Gate 1 输入 PDF";
  }
}

async function downloadGuidedSummary() {
  if (!state.designId || !state.guidedExportUrl || !apiBase()) return;
  try {
    const response = await authorizedDesignDownload(state.guidedExportUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ece329-guided-summary-${state.designId}.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("你的设计总结已导出");
  } catch (error) {
    console.warn("Unable to download guided summary", error);
    showToast("总结导出失败，请稍后重试");
  }
}

async function sendVersionAction(versionRequest, message) {
  if (!state.designId || state.sessionKind !== "api" || dom.sendButton.disabled) return;
  const requestGeneration = designGeneration;
  state.pendingRequest = {
    turnId: crypto.randomUUID(),
    message,
    uiAction: null,
    versionRequest,
  };
  addMessage("user", message);
  setBusy(true);
  showTyping();
  try {
    const response = await sendToApi(message, null);
    if (requestGeneration !== designGeneration) {
      hideTyping();
      return;
    }
    response._runtime_source = "api";
    hideTyping();
    applyResponse(response, message);
  } catch (error) {
    hideTyping();
    if (requestGeneration !== designGeneration) return;
    console.warn("Unable to apply version action", error);
    addMessage(
      "assistant",
      "这次版本操作没有完成，当前设计没有改变。你可以稍后重试。",
      ["版本操作未完成"],
      { meta: "ECE329 Agent" },
    );
  } finally {
    setBusy(false);
    render();
    saveState();
  }
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
  const requestGeneration = designGeneration;

  const isPendingReplay = replayingPendingRequest && Boolean(state.pendingRequest);
  replayingPendingRequest = false;
  const uiAction = isPendingReplay
    ? state.pendingRequest.uiAction || null
    : state.pendingUiAction;
  const isUiAdvance = uiAction === "ADVANCE_STAGE";
  const selectedOptionId = isPendingReplay
    ? state.pendingRequest.optionId || null
    : state.pendingOptionId;
  if (
    !state.pendingRequest
    || state.pendingRequest.message !== message
    || state.pendingRequest.uiAction !== uiAction
  ) {
    state.pendingRequest = {
      turnId: crypto.randomUUID(),
      message,
      uiAction,
      optionId: selectedOptionId,
      versionRequest: null,
    };
  }
  state.pendingUiAction = null;
  if (state.stageIndex === 0 && !isUiAdvance && !state.pendingDirection) {
    state.pendingDirection = message;
  }
  const isSummaryContribution = (
    state.stageIndex === STAGES.length - 1 && !isUiAdvance
  );
  state.lastStudentInput = message;
  if (!isPendingReplay) addMessage("user", message);
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
      response = await sendToApi(message, uiAction);
      response._runtime_source = "api";
    } else if (!apiBase()) {
      await wait(420);
      response = createDemoResponse(message, uiAction);
    } else {
      throw lastConnectionError || new ApiError(
        "Backend is not ready",
        0,
        "backend_unavailable",
      );
    }
    if (requestGeneration !== designGeneration) {
      hideTyping();
      return;
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
    if (requestGeneration !== designGeneration) return;
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
      state.quickActions = [pendingRequestRetryAction()];
      return;
    }
    if (error instanceof ApiError && error.code === "session_conflict") {
      await reloadApiDesignState();
      // A conflicting idempotency key must never be reused for the next attempt.
      state.pendingRequest = {
        ...(state.pendingRequest || {}),
        turnId: crypto.randomUUID(),
        message,
        uiAction,
        optionId: selectedOptionId,
      };
      addMessage(
        "assistant",
        "设计可能已在另一个窗口更新。我已同步当前设计，请重新发送本轮内容。",
        ["状态已刷新"],
        { meta: "ECE329 Agent" },
      );
      state.quickActions = [pendingRequestRetryAction()];
      return;
    }
    const failure = requestFailurePresentation(error);
    setConnectionState("error", failure.badge);
    dom.offlineNotice.hidden = false;
    dom.offlineNotice.querySelector("strong").textContent = failure.badge;
    dom.offlineNotice.querySelector("span").textContent = failure.notice;
    addMessage(
      "assistant",
      failure.message,
      [failure.tag],
      { meta: "ECE329 Agent" },
    );
    state.quickActions = [pendingRequestRetryAction()];
    showToast(failure.toast);
  } finally {
    setBusy(false);
    render();
    saveState();
    dom.chatInput.focus();
  }
}

async function sendToApi(message, uiAction = null) {
  if (!state.designId || state.sessionKind === "demo" || state.designId.startsWith("demo_")) {
    return createApiDesign(message);
  }
  const turn = buildTurnRequest(message, uiAction, state.pendingRequest?.versionRequest || null);
  return authorizedDesignApiRequest(`/v1/designs/${encodeURIComponent(state.designId)}/turns`, {
    method: "POST",
    headers: {
      "Idempotency-Key": turn.turn_id,
    },
    body: JSON.stringify(turn),
  });
}

async function createApiDesign(message) {
  const requestGeneration = designGeneration;
  const idempotencyKey = state.pendingRequest?.turnId || crypto.randomUUID();
  const request = () => apiRequest("/v1/designs", {
      method: "POST",
      headers: { ...courseAccessHeaders(), "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ idea: message }),
    });
  try {
    return await request();
  } catch (error) {
    if (requestGeneration !== designGeneration) throw error;
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
  invalidateDesignRequests();
  state = { ...initialState(), messages: retainedMessages };
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
  localStorage.removeItem(DESIGN_RESUME_KEY);
  saveState();
}

async function ensureDesignAccessToken() {
  const token = sessionStorage.getItem(DESIGN_TOKEN_KEY) || "";
  if (token || !state.designId) return token;
  const requestGeneration = designGeneration;
  const resumeToken = localStorage.getItem(DESIGN_RESUME_KEY) || "";
  if (!resumeToken) return "";
  const restored = await apiRequest(
    `/v1/designs/${encodeURIComponent(state.designId)}/resume`,
    {
      method: "POST",
      body: JSON.stringify({ resume_token: resumeToken }),
    },
  );
  if (requestGeneration !== designGeneration) return "";
  applyDesignSnapshot(restored);
  return sessionStorage.getItem(DESIGN_TOKEN_KEY) || "";
}

async function authorizedDesignApiRequest(path, options = {}) {
  const requestGeneration = designGeneration;
  let token = await ensureDesignAccessToken();
  if (requestGeneration !== designGeneration) {
    throw new DOMException("Design session changed", "AbortError");
  }
  if (!token) {
    throw new ApiError("Missing design access token", 401, "access_denied");
  }
  const request = () => apiRequest(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${token}`,
    },
  });
  try {
    return await request();
  } catch (error) {
    if (requestGeneration !== designGeneration) throw error;
    if (!(error instanceof ApiError) || error.code !== "access_denied") throw error;
    // Another tab may have rotated this tab's short-lived access token. The
    // shared resume credential remains the source for a one-time recovery.
    sessionStorage.removeItem(DESIGN_TOKEN_KEY);
    token = await ensureDesignAccessToken();
    if (!token) throw error;
    return request();
  }
}

async function authorizedDesignDownload(path) {
  const requestGeneration = designGeneration;
  let token = await ensureDesignAccessToken();
  if (requestGeneration !== designGeneration) {
    throw new DOMException("Design session changed", "AbortError");
  }
  if (!token) {
    throw new ApiError("Missing design access token", 401, "access_denied");
  }
  const request = () => fetch(`${apiBase()}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  let response = await request();
  if (response.status !== 401) return response;
  if (requestGeneration !== designGeneration) return response;
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
  token = await ensureDesignAccessToken();
  if (!token) return response;
  response = await request();
  return response;
}

async function reloadApiDesignState() {
  if (!state.designId) return;
  const requestGeneration = designGeneration;
  const designId = state.designId;
  try {
    const design = await authorizedDesignApiRequest(`/v1/designs/${encodeURIComponent(designId)}`, {
      method: "GET",
    });
    if (requestGeneration !== designGeneration) return;
    applyDesignSnapshot(design);
  } catch (error) {
    if (requestGeneration !== designGeneration) return;
    console.warn("Unable to refresh design state", error);
  }
}

function applyDesignSnapshot(design) {
  if (design.design_access_token) {
    sessionStorage.setItem(DESIGN_TOKEN_KEY, design.design_access_token);
  }
  if (design.design_resume_token) {
    localStorage.setItem(DESIGN_RESUME_KEY, design.design_resume_token);
  }
  if (design.design_id) {
    state.designId = design.design_id;
    state.sessionKind = "api";
  }
  state.mode = design.interaction_state || state.mode;
  const index = STAGES.findIndex(([id]) => id === design.current_stage);
  if (index >= 0) state.stageIndex = index;
  if (design.task_report) state.taskReport = design.task_report;
  if (design.builder_handoff_status) {
    state.builderHandoffStatus = design.builder_handoff_status;
  }
  if (design.quality_review) state.qualityReview = design.quality_review;
  if (design.recent_versions) state.recentVersions = design.recent_versions;
  state.reportReady = design.report_ready === true;
  state.reportUrl = typeof design.report_url === "string" ? design.report_url : null;
  state.builderInputReady = design.builder_input_ready === true;
  state.builderInputUrl = typeof design.builder_input_url === "string"
    ? design.builder_input_url
    : null;
  state.guidedExportReady = design.guided_export_ready === true;
  state.guidedExportUrl = typeof design.guided_export_url === "string"
    ? design.guided_export_url
    : null;
  saveState();
  render();
}

function buildTurnRequest(message, uiAction = null, versionRequest = null) {
  const turnId = state.pendingRequest?.turnId || crypto.randomUUID();
  const turn = { message, turn_id: turnId };
  if (versionRequest) turn.version_request = versionRequest;
  const optionId = state.pendingRequest?.optionId || state.pendingOptionId;
  if (optionId) {
    turn.selected_option_id = optionId;
  }
  if (uiAction !== "ADVANCE_STAGE") return turn;

  turn.complete_stage = true;
  if (state.mode === "EMVR_DIRECT") return turn;
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
    if (summary.length >= 20 && sections.length >= 1) {
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
      title: "让一支探针穿过看不见的场",
      picture: "想象一支能够显示方向和大小的探针缓慢穿过实验区域，每到一个位置就留下一个箭头和读数。把整条路径上的记录排在一起后，原本看不见的空间变化会怎样变成有峰值、低谷或方向翻转的轨迹？",
      extension: "可以让探针沿直线、圆弧或闭合路径移动，比较不同路线突出哪些特征。",
    },
    {
      title: "把三维空间切成一层层剖面",
      picture: "想象把一个复杂的场或波分布切成许多平行薄层，并把每层上的方向、强弱或相位用颜色显示。连续翻动这些剖面时，集中区域、节点或方向变化会突然出现、移动，还是在多层之间保持相同形状？",
      extension: "可以改变剖面的方向或间距，看看哪些空间特征会变得更清楚。",
    },
    {
      title: "把两个可调条件铺成一张响应地图",
      picture: "想象横向改变一个条件、纵向改变另一个条件，每一种组合都在地图上留下一个颜色格子。整张图填满后，强响应形成山脊，弱响应形成谷地，还可能出现孤立峰值或弯曲分界。",
      extension: "可以在地图上加入一条理论预测路径，比较沿路径观察和浏览完整参数空间得到的认识。",
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
  const blocks = scenes.map((scene) => [
    `${scene.label}｜${scene.title}`,
    scene.physical_picture,
    `启发性延伸：${scene.illustrative_extension}`,
    `可以继续想：${scene.thinking_prompt}`,
    `组合提示：${scene.combination_seed}`,
  ].join("\n")).join("\n\n");
  return `${blocks}\n\n如果这三个图景都没有引起你的兴趣，也没关系。我还可以从其他ECE329课程关系中再为你展示一组不同的图景。`;
}

function demoBreadthTask() {
  return "哪幅图景触发了你的联想，或者你想怎样组合、替换其中的对象，提出一个自己的ECE329课内设想？";
}

function createDemoResponse(message, uiAction = null) {
  const firstTurn = !state.designId;
  let guidedStageEntered = false;
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
  const advanceRequested = uiAction === "ADVANCE_STAGE";
  // This is the only text marker handled directly by the page. All other
  // conversational intent belongs to the connected API's contextual resolver.
  const emvr = inputCategory !== "UNREASONABLE_REQUEST"
    && (uiAction === "SET_EMVR_DIRECT" || message.toLocaleUpperCase().includes("EMVR"));
  const needsBreadthSupport = inputCategory === "AMBIGUOUS";
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
    } else if (needsBreadthSupport) {
      guidedIntroduction = "好的，那我来帮助你拓展思路。暂时没有具体方向也没关系。我们可以先从ECE329课上学习的电磁场、电磁波和传输线中寻找你感兴趣的关系。";
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

  if (emvr && state.mode !== "EMVR_DIRECT") {
    state.mode = "EMVR_DIRECT";
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
      quick_actions: status.complete
        ? [advanceQuickAction("确认想法完善并进入变量与条件")]
        : [],
      warnings: [],
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
        ? [advanceQuickAction("确认想法完善并进入变量与条件")]
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
    const referenceSteps = DEMO_GUIDED_STAGE_REFERENCE_STEPS[stageId] || [];
    const referenceText = referenceSteps.length
      ? `我先把已有线索顺成一份可以随手修改的参考：\n${referenceSteps.map((step, index) => `${index + 1}. ${step}`).join("\n")}\n\n觉得合适的部分可以留下；想改哪里，直接告诉我就行。\n\n`
      : "";
    return {
      assistant_message: `好，前面的实验想法已经保留下来了。我们接着看看“${currentWorkspaceTitle(state.stageIndex)}”。\n\n${referenceText}${DEMO_GUIDED_STAGE_ENTRY_QUESTIONS[stageId] || "先说说你对这一部分的想法，我会在这个基础上继续帮你完善。"}`,
      student_task: null,
      current_stage: stageId,
      handled_stage: stageId,
      interaction_state: state.mode,
      stage_payload: {
        guided_entry: true,
        awaiting_student_description: true,
        preserved_idea_summary: preservedIdea,
        reference_draft: referenceSteps,
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
  if (state.mode === "GUIDED_DESIGN" && state.stageIndex === STAGES.length - 1) {
    if (!advanceRequested && message.trim()) {
      state.summarySections = [message.trim()];
      state.pendingSummary = message.trim();
      return {
        assistant_message: "你已经把研究问题、主要比较、预期现象和课程关系串起来了。我按你的原意保存，这次实验设计到这里就完成了。",
        current_stage: STAGES[state.stageIndex][0],
        handled_stage: STAGES[state.stageIndex][0],
        interaction_state: state.mode,
        workflow_status: "complete",
        stage_payload: {
          student_summary_confirmed: true,
          final_proposal_generated: false,
        },
        quick_actions: [],
        warnings: [],
        _runtime_source: "demo",
      };
    }
    return {
      assistant_message: "请直接写出你想保留的总结；写完后这次实验设计就完成了。",
      student_task: null,
      current_stage: STAGES[state.stageIndex][0],
      handled_stage: STAGES[state.stageIndex][0],
      interaction_state: state.mode,
      stage_payload: {
        awaiting_student_summary: true,
        final_proposal_generated: false,
      },
      quick_actions: [],
      warnings: [],
      _runtime_source: "demo",
    };
  }
  return {
    assistant_message: `明白，我们就沿着ECE329课上所学的“${evidence.title}”继续。${prompt}`,
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
  // The static preview has no semantic model. Unknown text remains ambiguous
  // and receives breadth support; it is never classified by command phrases.
  return directEvidence ? "COURSE_CONTENT" : "AMBIGUOUS";
}

function resolveDemoOptionReference(_text, options, selectedOptionId = null) {
  if (!Array.isArray(options) || !options.length) return null;
  const normalizedOptions = options.map(normalizeQuickAction);
  if (selectedOptionId) {
    const selected = normalizedOptions.find((option) => option.optionId === selectedOptionId);
    if (selected) return selected;
  }
  return null;
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

function updateDemoStandardComparisonDecisions(_text, comparisons, _controlTurn = false) {
  // The static GitHub Pages demo has no semantic model. It preserves the
  // current proposal and leaves natural-language edits to the connected API.
  return (comparisons || []).map((comparison) => {
    return {
      ...comparison,
      cases: [...(comparison.cases || [])],
      recommended_cases: [...(comparison.recommended_cases || comparison.cases || [])],
    };
  });
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
      ? `这组对照先作为建议保留：${recommended.join("与")}。如果符合你的想法，可以直接沿用；想删掉或替换其中一种也可以直接说。`
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
    ["learning_objective", "学习目标", false, ""],
    ["research_question", "研究问题", false, ""],
    ["theoretical_framework", "理论依据", true, "根据课程资料匹配的核心理论关系"],
    ["hypothesis", "假设与预期趋势", false, ""],
    ["conceptual_structure", "概念实验结构", false, ""],
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
  status.last_clarified_facet_ids = [...new Set(clarified)];
  refreshDemoIdeaDevelopmentStatus(status);
}

function isSubstantiveDemoFacetAnswer(_facetId, message) {
  const normalized = String(message || "").replace(/[\s，,。；;！!？?]/g, "");
  return normalized.length >= 6;
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
  if (firstReview) {
    return `这个方向已经有了可以继续发展的实验雏形。接下来先把“${title}”说清楚：${demoIdeaDevelopmentTask(status)}`;
  }
  const transitions = {
    research_question: "接下来把这个想法收成一个能回答的研究问题：",
    learning_objective: "接下来看看这个实验最终要帮助你弄懂什么：",
    hypothesis: "接下来做一个有物理依据的预测：",
    conceptual_structure: "接下来把实验中需要出现的对象和关系理清：",
  };
  return `${transitions[status.active_facet_id] || `接下来看看“${title}”：`}${demoIdeaDevelopmentTask(status)}`;
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
  const titles = clarifiedFacetIds
    .map((facetId) => status.facets.find((facet) => facet.facet_id === facetId)?.title)
    .filter(Boolean);
  if (titles.length === 1 && titles[0] === "学习目标") {
    return "这个学习目标很清楚，后面的设计需要真正帮助你解释这条物理关系。";
  }
  if (titles.length === 1 && titles[0] === "研究问题") {
    return "这个研究问题已经很具体，比较条件和准备观察的现象都对上了。";
  }
  if (titles.length === 1 && titles[0] === "假设与预期趋势") {
    return "你的预测已经同时给出了现象和物理理由，我们可以接着往下完善。";
  }
  return `这部分已经把${titles.map((title) => `“${title}”`).join("、")}说明得更具体。`;
}

function applyResponse(response, userMessage) {
  if (response.design_access_token) {
    sessionStorage.setItem(DESIGN_TOKEN_KEY, response.design_access_token);
  }
  if (response.design_resume_token) {
    localStorage.setItem(DESIGN_RESUME_KEY, response.design_resume_token);
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
  if (response.task_report) state.taskReport = response.task_report;
  if (response.builder_handoff_status) {
    state.builderHandoffStatus = response.builder_handoff_status;
  }
  if (response.quality_review) state.qualityReview = response.quality_review;
  if (response.stage_payload?.quality_review) {
    state.qualityReview = response.stage_payload.quality_review;
  }
  if (response.stage_payload?.version_control) {
    state.versionControl = response.stage_payload.version_control;
    const last = Array.isArray(state.versionControl)
      ? state.versionControl.at(-1)
      : state.versionControl;
    if (last?.action === "VIEW_RECENT") state.recentVersions = last;
  }
  state.reportReady = response.report_ready === true;
  state.reportUrl = typeof response.report_url === "string" ? response.report_url : null;
  state.builderInputReady = response.builder_input_ready === true;
  state.builderInputUrl = typeof response.builder_input_url === "string"
    ? response.builder_input_url
    : null;
  state.guidedExportReady = response.guided_export_ready === true;
  state.guidedExportUrl = typeof response.guided_export_url === "string"
    ? response.guided_export_url
    : null;
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
  state.pendingRequest = null;
  state.pendingOptionId = null;
  state.pendingUiAction = null;
  renderTaskReport();
  renderQualityReview();
}

function composeAssistantText(response) {
  const base = response.assistant_message || response.message || "这一部分已经整理好了。";
  const parts = [base];
  const shouldShowStudentTask = (
    state.mode === "EMVR_DIRECT" || state.stageIndex !== 0
  );
  const studentTask = shouldShowStudentTask && typeof response.student_task === "string"
    ? response.student_task.trim()
    : "";
  if (studentTask && !base.includes(studentTask)) parts.push(studentTask);
  const warnings = Array.isArray(response.warnings)
    ? response.warnings.filter((item) => typeof item === "string" && item.trim())
    : [];
  if (warnings.length) parts.push(`提示：${warnings.join("；")}`);
  if (response.completion_error && state.stageIndex !== 0) {
    parts.push(`这一步还差一点：${response.completion_error}`);
  }
  return parts.join("\n\n");
}

function deriveQuickActions(response) {
  if (response.quick_actions) return response.quick_actions;
  if (response.workflow_status === "complete" || response.status === "complete") return [];
  const formulaCards = response.stage_payload?.formula_cards;
  if (Array.isArray(formulaCards) && formulaCards.length) {
    return formulaCards
      .map((card) => ({ option_id: card.option_id || null, label: card.title }))
      .filter((item) => item.option_id && item.label)
      .slice(0, 4);
  }
  const compositionOptions = response.stage_payload?.composition_options;
  if (Array.isArray(compositionOptions) && compositionOptions.length) {
    return compositionOptions.filter((item) => item?.option_id && item?.label);
  }
  const experimentMethods = response.stage_payload?.experiment_methods;
  if (Array.isArray(experimentMethods) && experimentMethods.length) {
    return experimentMethods
      .map((method, index) => ({
        option_id: method.option_id || null,
        label: `方法${index + 1}｜${method.title || "实验方案"}`,
      }))
      .filter((item) => item.option_id && item.label)
      .slice(0, 15);
  }
  if (response.current_stage && response.handled_stage && response.current_stage !== response.handled_stage) {
    return [`开始${currentWorkspaceTitle(state.stageIndex)}`];
  }
  const clarificationChoices = response.stage_payload?.clarification_choices;
  if (Array.isArray(clarificationChoices) && clarificationChoices.length) {
    return clarificationChoices;
  }
  if (state.mode === "EMVR_DIRECT") {
    if (response.stage_payload?.awaiting_user_design_input === true) return [];
    return [advanceQuickAction("保留这部分并继续")];
  }
  if (response.stage_payload?.awaiting_student_description === true) return [];

  if (state.stageIndex === 0) {
    const development = response.stage_payload?.idea_development_status;
    if (development) {
      return development.complete
        ? [advanceQuickAction("确认想法完善并进入变量与条件")]
        : [];
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
    return [];
  }
  return [advanceQuickAction(guidedAdvanceLabel(state.stageIndex))];
}

function advanceQuickAction(label) {
  return { label, action: "ADVANCE_STAGE" };
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
  const shouldDeleteApiDesign = Boolean(
    apiBase() && designId && token && state.sessionKind === "api"
  );
  invalidateDesignRequests();
  state = initialState();
  sessionStorage.removeItem(DESIGN_TOKEN_KEY);
  localStorage.removeItem(DESIGN_RESUME_KEY);
  saveState();
  setBusy(false);
  render();
  if (shouldDeleteApiDesign) {
    void apiRequest(`/v1/designs/${encodeURIComponent(designId)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    }).catch((error) => console.warn("Unable to delete backend design", error));
  }
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
  state.pendingUiAction = null;
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
dom.downloadReportButton.addEventListener("click", downloadTaskReport);
dom.downloadBuilderInputButton.addEventListener("click", downloadBuilderInput);
dom.downloadGuidedSummaryButton.addEventListener("click", downloadGuidedSummary);
dom.viewVersionsButton.addEventListener("click", () => sendVersionAction(
  { action: "VIEW_RECENT" },
  "查看最近的设计修改",
));
dom.undoVersionButton.addEventListener("click", () => sendVersionAction(
  { action: "UNDO_LAST" },
  "撤销上一项设计修改",
));
window.addEventListener("resize", drawChart);

async function initializePage() {
  setBusy(true);
  render();
  try {
    await checkConnection();
    if (connectionState === "online" && state.designId && state.sessionKind === "api") {
      await reloadApiDesignState();
    }
  } finally {
    if (state.pendingRequest) {
      state.quickActions = [pendingRequestRetryAction()];
    }
    setBusy(false);
    render();
    saveState();
  }
}

void initializePage();
