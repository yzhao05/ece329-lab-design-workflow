from __future__ import annotations

import io
import json
import unittest
import uuid
from pathlib import Path
from typing import Any

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, SessionConflict
from ece329_workflow.security import APISettings, FixedWindowRateLimiter
from ece329_workflow.store import SQLiteSessionStore
from tools.configure_pages_api import configure_api_url, normalize_https_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"
TEST_TEMP_ROOT.mkdir(exist_ok=True)


def workspace_temp_path(suffix: str) -> Path:
    return TEST_TEMP_ROOT / f"{uuid.uuid4().hex}{suffix}"


def remove_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-shm"), Path(f"{path}-wal")):
        candidate.unlink(missing_ok=True)


def call_api(
    api: WorkflowAPI,
    method: str,
    path: str,
    body: dict[str, Any] | bytes | None = None,
    origin: str = "",
    remote_addr: str = "127.0.0.1",
    request_headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    if isinstance(body, dict):
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    else:
        raw_body = body or b""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(raw_body)),
        "CONTENT_TYPE": "application/json",
        "REMOTE_ADDR": remote_addr,
        "wsgi.input": io.BytesIO(raw_body),
    }
    if origin:
        environ["HTTP_ORIGIN"] = origin
    for name, value in (request_headers or {}).items():
        environ[f"HTTP_{name.upper().replace('-', '_')}"] = value
    response_body = b"".join(api(environ, start_response))
    payload = json.loads(response_body.decode("utf-8")) if response_body else {}
    return captured["status"], captured["headers"], payload


class APISecurityTests(unittest.TestCase):
    def make_api(self, settings: APISettings) -> WorkflowAPI:
        return WorkflowAPI(
            WorkflowEngine(generator=RuleBasedStageGenerator()),
            settings=settings,
            rate_limiter=FixedWindowRateLimiter(
                settings.rate_limit_requests,
                settings.rate_limit_window_seconds,
            ),
        )

    def test_allowed_origin_receives_specific_cors_header(self) -> None:
        origin = "https://student.github.io"
        api = self.make_api(APISettings(allowed_origins=(origin,)))

        status, headers, payload = call_api(api, "GET", "/health", origin=origin)

        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
        self.assertEqual(headers["Vary"], "Origin")
        self.assertEqual(payload["storage"]["provider"], "memory")

    def test_unlisted_origin_is_rejected(self) -> None:
        api = self.make_api(APISettings(allowed_origins=("https://allowed.example",)))

        status, headers, payload = call_api(
            api,
            "GET",
            "/health",
            origin="https://untrusted.example",
        )

        self.assertTrue(status.startswith("403"))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(payload["error"], "origin_not_allowed")

    def test_environment_rejects_origin_with_repository_path(self) -> None:
        with self.assertRaises(ValueError):
            APISettings.from_environment(
                {"ECE329_ALLOWED_ORIGINS": "https://student.github.io/repository"}
            )

    def test_post_rate_limit_returns_retry_after(self) -> None:
        settings = APISettings(rate_limit_requests=1, rate_limit_window_seconds=60)
        api = self.make_api(settings)

        first_status, _, _ = call_api(api, "POST", "/v1/designs", {"idea": "研究驻波"})
        second_status, headers, payload = call_api(api, "POST", "/v1/designs", {"idea": "研究偏振"})

        self.assertTrue(first_status.startswith("201"))
        self.assertTrue(second_status.startswith("429"))
        self.assertIn("Retry-After", headers)
        self.assertEqual(payload["error"], "rate_limit_exceeded")

    def test_oversized_request_is_rejected_before_json_parsing(self) -> None:
        api = self.make_api(APISettings(max_body_bytes=8))

        status, _, payload = call_api(api, "POST", "/v1/designs", b"123456789")

        self.assertTrue(status.startswith("413"))
        self.assertEqual(payload["error"], "request_too_large")

    def test_empty_and_overlong_student_messages_are_rejected(self) -> None:
        api = self.make_api(APISettings(max_text_chars=12))

        empty_status, empty_headers, empty_payload = call_api(
            api,
            "POST",
            "/v1/designs",
            {"idea": "   "},
        )
        long_status, _, long_payload = call_api(
            api,
            "POST",
            "/v1/designs",
            {"idea": "x" * 13},
        )

        self.assertTrue(empty_status.startswith("400"))
        self.assertTrue(long_status.startswith("400"))
        self.assertEqual(empty_payload["error"], "invalid_request")
        self.assertEqual(long_payload["error"], "invalid_request")
        self.assertEqual(empty_headers["Cache-Control"], "no-store")
        self.assertEqual(empty_headers["X-Content-Type-Options"], "nosniff")

    def test_course_access_code_protects_design_creation(self) -> None:
        api = self.make_api(APISettings(access_code="course-secret"))

        denied, _, denied_payload = call_api(api, "POST", "/v1/designs", {"idea": "研究驻波"})
        allowed, _, allowed_payload = call_api(
            api,
            "POST",
            "/v1/designs",
            {"idea": "研究驻波"},
            request_headers={"X-ECE329-Access-Code": "course-secret"},
        )

        self.assertTrue(denied.startswith("401"))
        self.assertEqual(denied_payload["error"], "access_denied")
        self.assertTrue(allowed.startswith("201"))
        self.assertIn("design_access_token", allowed_payload)

    def test_design_token_protects_session_routes(self) -> None:
        api = self.make_api(APISettings())
        _, _, created = call_api(api, "POST", "/v1/designs", {"idea": "研究驻波"})
        path = f"/v1/designs/{created['design_id']}"

        denied, _, _ = call_api(api, "GET", path)
        allowed, _, payload = call_api(
            api,
            "GET",
            path,
            request_headers={"Authorization": f"Bearer {created['design_access_token']}"},
        )

        self.assertTrue(denied.startswith("401"))
        self.assertTrue(allowed.startswith("200"))
        self.assertEqual(payload["design_id"], created["design_id"])
        self.assertNotIn("access_token_hash", json.dumps(payload))

    def test_malformed_request_types_return_400(self) -> None:
        api = self.make_api(APISettings())
        for body in ({"idea": 329}, {"idea": ["驻波"]}):
            status, _, payload = call_api(api, "POST", "/v1/designs", body)
            self.assertTrue(status.startswith("400"))
            self.assertEqual(payload["error"], "invalid_request")

        _, _, created = call_api(api, "POST", "/v1/designs", {"idea": "驻波"})
        headers = {"Authorization": f"Bearer {created['design_access_token']}"}
        path = f"/v1/designs/{created['design_id']}/turns"
        invalid_turns = [
            {"message": None},
            {"message": "继续", "complete_stage": "false"},
            {"message": "继续", "context_patch": []},
        ]
        for body in invalid_turns:
            status, _, payload = call_api(api, "POST", path, body, request_headers=headers)
            self.assertTrue(status.startswith("400"))
            self.assertEqual(payload["error"], "invalid_request")


class SQLiteStoreTests(unittest.TestCase):
    def test_session_persists_across_store_instances(self) -> None:
        path = workspace_temp_path(".sqlite3")
        try:
            first_store = SQLiteSessionStore(path)
            session = DesignSession(
                design_id="design_persistent",
                interaction_state=InteractionState.GUIDED_DESIGN,
                design_context={"idea": {"original": "研究驻波"}},
            )
            first_store.save(session)

            loaded = SQLiteSessionStore(path).get(session.design_id)

            self.assertEqual(loaded.design_context, session.design_context)
            self.assertEqual(loaded.revision, 0)
        finally:
            remove_sqlite_files(path)

    def test_optimistic_revision_rejects_stale_save(self) -> None:
        path = workspace_temp_path(".sqlite3")
        try:
            store = SQLiteSessionStore(path)
            session = DesignSession(
                design_id="design_conflict",
                interaction_state=InteractionState.GUIDED_DESIGN,
            )
            store.save(session)
            first = store.get(session.design_id)
            stale = store.get(session.design_id)
            first.revision = 1
            store.save(first, expected_revision=0)
            stale.revision = 1

            with self.assertRaises(SessionConflict):
                store.save(stale, expected_revision=0)
        finally:
            remove_sqlite_files(path)


class PagesConfigurationTests(unittest.TestCase):
    def test_build_injection_keeps_source_configurable(self) -> None:
        config = workspace_temp_path("-config.js")
        try:
            config.write_text(
                '/* Example: API_BASE_URL: "https://wrong.example" */\n'
                'window.X = {\n  API_BASE_URL: "",\n};\n',
                encoding="utf-8",
            )

            configure_api_url(config, "https://api.example.edu/")

            rendered = config.read_text(encoding="utf-8")
            self.assertIn('API_BASE_URL: "https://api.example.edu"', rendered)
            self.assertIn('API_BASE_URL: "https://wrong.example"', rendered)
        finally:
            config.unlink(missing_ok=True)

    def test_build_injection_rejects_non_https_url(self) -> None:
        with self.assertRaises(ValueError):
            normalize_https_url("http://api.example.edu")

    def test_build_injection_rejects_url_credentials(self) -> None:
        with self.assertRaises(ValueError):
            normalize_https_url("https://user:secret@api.example.edu")


if __name__ == "__main__":
    unittest.main()
