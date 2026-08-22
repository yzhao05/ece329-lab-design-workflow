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
        self.assertIn("summary.length >= 20", self.app_js)

    def test_failed_api_request_has_real_demo_fallback(self) -> None:
        self.assertIn("const fallback = createDemoResponse(message)", self.app_js)
        self.assertIn("已自动切换为本地演示回答", self.app_js)

    def test_api_student_task_and_warnings_are_visible(self) -> None:
        self.assertIn("function composeAssistantText(response)", self.app_js)
        self.assertIn("response.student_task", self.app_js)
        self.assertIn("response.warnings", self.app_js)
        self.assertIn("response.completion_error", self.app_js)

    def test_frontend_keeps_design_token_out_of_persistent_storage(self) -> None:
        self.assertIn("sessionStorage.setItem(DESIGN_TOKEN_KEY", self.app_js)
        self.assertIn("Authorization: `Bearer ${token}`", self.app_js)
        self.assertIn("X-ECE329-Access-Code", self.app_js)

    def test_stale_backend_session_is_cleared_without_fake_continuation(self) -> None:
        self.assertIn("function clearApiSession()", self.app_js)
        self.assertIn('["session_not_found", "access_denied"]', self.app_js)

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


if __name__ == "__main__":
    unittest.main()
