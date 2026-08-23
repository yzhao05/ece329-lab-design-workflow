from __future__ import annotations

import re
import unittest
import json
from pathlib import Path

from ece329_workflow.knowledge_base import KNOWLEDGE


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"


class WebFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = (DOCS_ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        cls.config_js = (DOCS_ROOT / "assets" / "config.js").read_text(encoding="utf-8")
        cls.index_html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8")
        cls.styles_css = (DOCS_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        cls.pages_workflow = (PROJECT_ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        cls.ci_workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cls.dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    def test_public_config_keeps_api_base_url_blank(self) -> None:
        self.assertIn('API_BASE_URL: ""', self.config_js)

    def test_pages_build_injects_api_url_from_repository_variable(self) -> None:
        self.assertIn("vars.ECE329_API_BASE_URL", self.pages_workflow)
        self.assertIn("tools/configure_pages_api.py", self.pages_workflow)

    def test_guided_confirmation_builds_completion_context(self) -> None:
        self.assertIn("function buildTurnRequest(message)", self.app_js)
        self.assertIn("turn.complete_stage = true", self.app_js)
        self.assertIn("student_confirmed: true", self.app_js)
        self.assertIn("student_summary_complete: true", self.app_js)
        self.assertIn("student_summary: summary", self.app_js)
        self.assertIn("response.request_rejected !== true", self.app_js)
        self.assertIn("summary.length >= 20", self.app_js)

    def test_first_seven_steps_render_as_one_expandable_public_stage(self) -> None:
        self.assertIn('title: "实验想法完善"', self.app_js)
        self.assertIn("IDEA_DEVELOPMENT_STAGE_IDS", self.app_js)
        self.assertIn("WORKFLOW_GROUPS", self.app_js)
        self.assertIn("stage-substeps", self.app_js)
        self.assertIn("阶段 1 / 7", self.index_html)
        self.assertIn("DYNAMIC_COMPLETENESS", self.app_js)
        self.assertIn("ideaDevelopmentStatus", self.app_js)
        self.assertIn("active_facet_id", self.app_js)
        self.assertIn("确认想法完善并进入变量与条件", self.app_js)
        self.assertNotIn("确认课程映射并继续小点3", self.app_js)
        self.assertEqual(
            self.index_html.count("v=20260823-semantic-stage-advance"),
            3,
        )

    def test_demo_rechecks_all_idea_facets_without_fixed_substep_order(self) -> None:
        self.assertIn("function updateDemoIdeaDevelopmentStatus", self.app_js)
        self.assertIn("function refreshDemoIdeaDevelopmentStatus", self.app_js)
        self.assertIn("missing_facet_ids", self.app_js)
        self.assertIn("function demoStudentFacingNextTurn", self.app_js)
        self.assertIn("我们继续沿着同一个实验方向往下完善", self.app_js)
        self.assertNotIn("这些内容属于同一个“实验想法完善”阶段，不按固定顺序逐项闯关", self.app_js)
        self.assertNotIn("现在进入“实验想法完善”的小点2", self.app_js)

    def test_natural_idea_completion_message_is_recognized(self) -> None:
        self.assertIn("const ideaTransition", self.app_js)
        self.assertIn("const blockedTransition", self.app_js)
        self.assertIn("const semanticTransition", self.app_js)
        self.assertIn("const completedIdeaConfirmation", self.app_js)
        self.assertIn("想法|方向|大纲", self.app_js)
        self.assertIn("变量与条件|下一阶段", self.app_js)

    def test_quick_actions_send_stable_option_id(self) -> None:
        self.assertIn("function normalizeQuickAction(action)", self.app_js)
        self.assertIn("state.pendingOptionId = optionId", self.app_js)
        self.assertIn("turn.selected_option_id = state.pendingOptionId", self.app_js)
        self.assertIn("option_id: item.option_id || null", self.app_js)

    def test_stage_one_confirmation_uses_server_preserved_focus(self) -> None:
        self.assertIn("response.stage_payload?.current_focus", self.app_js)
        self.assertIn("state.pendingDirection = serverFocus.trim()", self.app_js)
        self.assertIn("response.stage_payload?.idea_development_status", self.app_js)
        self.assertIn('inputCategory === "COURSE_CONTENT"', self.app_js)
        self.assertIn(
            'response.stage_payload?.input_category !== "COURSE_CONTENT"',
            self.app_js,
        )
        self.assertIn('phase === "BREADTH_EXPLORATION"', self.app_js)
        self.assertIn('phase === "INTEREST_DESCRIPTION"', self.app_js)
        self.assertIn("response.stage_payload?.idea_development_status", self.app_js)

    def test_stage_one_greeting_and_redirects_use_student_facing_course_language(self) -> None:
        self.assertIn("ECE329课上所学", self.app_js)
        self.assertNotIn(
            'text: "欢迎来到 ECE329 Lab Studio。我们先从讲义中的概念出发',
            self.app_js,
        )
        self.assertNotIn("Lecture-grounded", self.app_js)
        self.assertIn("不属于ECE329课程的内容范围", self.app_js)
        self.assertIn("我不能执行", self.app_js)
        self.assertIn("classifyDemoStageOneInput", self.app_js)
        self.assertIn("resolveDemoOptionReference", self.app_js)
        self.assertIn("function parseDemoOrdinal(raw)", self.app_js)
        self.assertIn("你已经把方向收到了", self.app_js)
        self.assertIn("请先用自己的话说说", self.app_js)
        self.assertIn('return "UNREASONABLE_REQUEST"', self.app_js)
        self.assertIn('return directEvidence ? "COURSE_CONTENT" : "OUT_OF_SCOPE"', self.app_js)
        self.assertIn(
            'emvrIntent === true && inputCategory !== "UNREASONABLE_REQUEST"',
            self.app_js,
        )
        self.assertIn("当前请求没有改变你的实验设计进度", self.app_js)
        self.assertIn(
            "[LEGACY_INITIAL_GREETING, PREVIOUS_INITIAL_GREETING].includes(message.text)",
            self.app_js,
        )

    def test_stage_one_demo_and_api_present_combinable_physical_scenes(self) -> None:
        self.assertIn("function createDemoExplorationScenes(evidence)", self.app_js)
        self.assertIn("function formatDemoExplorationScenes(scenes)", self.app_js)
        self.assertIn("ILLUSTRATIVE_ONLY_NOT_COURSE_EVIDENCE", self.app_js)
        self.assertIn("启发性延伸", self.app_js)
        self.assertIn("提出一个自己的ECE329课内设想", self.app_js)
        self.assertIn("response.stage_payload?.exploration_scenes", self.app_js)
        self.assertIn("scene.course_anchor?.option_id", self.app_js)
        self.assertIn("function resolveDemoSceneCombination(text, options)", self.app_js)
        self.assertIn("function inferDemoStandardComparisons(text)", self.app_js)
        self.assertIn("function updateDemoStandardComparisonDecisions", self.app_js)
        self.assertIn("function formatDemoStandardComparisons", self.app_js)
        self.assertIn("const DEMO_BASELINE_COMPARISONS", self.app_js)
        self.assertIn("function escapeRegularExpression", self.app_js)
        self.assertNotIn("const sameOnly", self.app_js)
        self.assertNotIn("const oppositeOnly", self.app_js)
        self.assertIn("建议默认把", self.app_js)
        self.assertIn('adoption_status: "PENDING"', self.app_js)
        self.assertNotIn("自动同时纳入", self.app_js)
        self.assertIn("共同解释什么核心现象", self.app_js)

    def test_failed_api_request_preserves_real_session(self) -> None:
        self.assertIn("当前设计已保留，请稍后重试", self.app_js)
        self.assertIn("await reloadApiDesignState()", self.app_js)
        self.assertNotIn("已自动切换为本地演示回答", self.app_js)

    def test_internal_stage_one_task_is_not_appended_to_chat(self) -> None:
        self.assertIn("function composeAssistantText(response)", self.app_js)
        self.assertIn("response.student_task", self.app_js)
        self.assertIn("const shouldShowStudentTask = state.stageIndex !== 0", self.app_js)
        self.assertIn("response.warnings", self.app_js)
        self.assertIn("response.completion_error", self.app_js)

    def test_frontend_keeps_design_token_out_of_persistent_storage(self) -> None:
        self.assertIn("sessionStorage.setItem(DESIGN_TOKEN_KEY", self.app_js)
        self.assertIn("Authorization: `Bearer ${token}`", self.app_js)
        self.assertIn("X-ECE329-Access-Code", self.app_js)

    def test_stale_backend_session_is_cleared_without_fake_continuation(self) -> None:
        self.assertIn("function clearApiSession()", self.app_js)
        self.assertIn('["session_not_found", "access_denied"]', self.app_js)
        self.assertIn("state = { ...initialState(), messages: retainedMessages }", self.app_js)
        self.assertIn("saveState();", self.app_js)

    def test_frontend_abuse_detection_is_platform_independent(self) -> None:
        self.assertIn("网站|平台|应用|服务|插件|频道|论坛|直播", self.app_js)
        self.assertNotIn("b站|哔哩哔哩|youtube|抖音", self.app_js)

    def test_hidden_connection_notice_is_not_overridden_by_flex_style(self) -> None:
        self.assertRegex(
            self.styles_css,
            r"\.offline-notice\[hidden\]\s*\{\s*display:\s*none;",
        )
        self.assertRegex(self.styles_css, r"\.message-list\s*\{\s*grid-row:\s*3;")
        self.assertRegex(self.styles_css, r"\.quick-actions\s*\{\s*grid-row:\s*4;")
        self.assertRegex(self.styles_css, r"\.composer\s*\{\s*grid-row:\s*5;")

    def test_browser_timeout_exceeds_backend_model_timeout(self) -> None:
        self.assertIn("REQUEST_TIMEOUT_MS: 70000", self.config_js)

    def test_visualization_response_is_saved_and_points_are_normalized(self) -> None:
        self.assertIn("state.visualization = response.visualization", self.app_js)
        self.assertIn("function normalizeChartPoints(points)", self.app_js)
        self.assertIn('canvas.dataset.source = hasApiPoints ? "api" : "demo"', self.app_js)
        self.assertIn('id="chartLegendLabel"', self.index_html)

    def test_stage_titles_show_emvr_only_in_emvr_mode(self) -> None:
        self.assertIn('["CONCEPTUAL_OR_VR_SETUP", "概念实验结构"]', self.app_js)
        self.assertIn('["STUDENT_SYNTHESIS_OR_EMVR_OUTPUT", "学生总结"]', self.app_js)
        self.assertIn('CONCEPTUAL_OR_VR_SETUP: "Unity VR模拟实验设计"', self.app_js)
        self.assertIn('STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: "EMVR方案汇总"', self.app_js)
        self.assertIn('state.mode === "EMVR_DIRECT"', self.app_js)
        self.assertIn("function stageTitle(index)", self.app_js)
        emvr_detector = self.app_js.split("function detectDemoEmvrIntent", 1)[1].split(
            "function isDemoNoDirectionRequest", 1
        )[0]
        self.assertNotIn("unity\\s*vr", emvr_detector)

    def test_published_frontend_contains_no_obvious_openai_secret(self) -> None:
        published_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in DOCS_ROOT.rglob("*")
            if path.is_file()
        )
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9_-]{20,}", published_text))

    def test_demo_formula_entries_match_canonical_catalog(self) -> None:
        formula_ids = {
            "load_reflection_phasor",
            "circular_polarization",
            "skin_depth",
            "capacitance_definition",
            "faraday_generalized_emf",
        }
        catalog = {item["id"]: item for item in KNOWLEDGE.formulas}
        for formula_id in formula_ids:
            self.assertIn(f'formulaId: "{formula_id}"', self.app_js)
            expression = json.dumps(catalog[formula_id]["expression"], ensure_ascii=False)[1:-1]
            self.assertIn(expression, self.app_js)

    def test_ci_checks_python_frontend_and_container(self) -> None:
        self.assertIn("python -m unittest discover", self.ci_workflow)
        self.assertIn("node --check docs/assets/app.js", self.ci_workflow)
        self.assertIn("docker build", self.ci_workflow)

    def test_container_healthcheck_uses_runtime_port(self) -> None:
        self.assertIn("os.getenv('PORT', '8080')", self.dockerfile)

    def test_container_context_keeps_runtime_knowledge_catalog(self) -> None:
        ignored_lines = {
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn("knowledge", ignored_lines)
        self.assertNotIn("src/ece329_workflow/knowledge", ignored_lines)
        self.assertIn("COPY src ./src", self.dockerfile)


if __name__ == "__main__":
    unittest.main()
