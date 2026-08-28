from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from http import HTTPStatus
from threading import RLock
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .engine import WorkflowEngine
from .models import DesignAccessDenied, SessionNotFound, StageCompletionError, WorkflowError
from .openai_generator import ModelServiceError
from .security import APISettings, FixedWindowRateLimiter


JsonHeaders = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
]


class RequestTooLarge(ValueError):
    pass


class WorkflowAPI:
    def __init__(
        self,
        engine: WorkflowEngine | None = None,
        settings: APISettings | None = None,
        rate_limiter: FixedWindowRateLimiter | None = None,
    ) -> None:
        self.engine = engine or WorkflowEngine()
        self.settings = settings or APISettings.from_environment()
        self.rate_limiter = rate_limiter or FixedWindowRateLimiter(
            self.settings.rate_limit_requests,
            self.settings.rate_limit_window_seconds,
        )
        self._create_idempotency: dict[str, dict[str, Any]] = {}
        self._create_idempotency_lock = RLock()
        self._create_request_locks: dict[str, RLock] = {}

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], Any],
    ) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        origin = str(environ.get("HTTP_ORIGIN", "")).strip().rstrip("/")
        cors_start_response = self._cors_start_response(start_response, origin)
        if not self.settings.allows_origin(origin):
            return self._respond(
                cors_start_response,
                HTTPStatus.FORBIDDEN,
                {"error": "origin_not_allowed"},
            )
        start_response = cors_start_response

        if method == "POST":
            allowed, retry_after = self.rate_limiter.allow(self._client_key(environ))
            if not allowed:
                return self._respond(
                    start_response,
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"error": "rate_limit_exceeded", "retry_after_seconds": retry_after},
                    [("Retry-After", str(retry_after))],
                )
        try:
            if method == "OPTIONS":
                return self._respond(start_response, HTTPStatus.NO_CONTENT, None)
            if method == "GET" and path == "/health":
                return self._respond(
                    start_response,
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "ece329-lab-design-workflow",
                        "generator": self.engine.generator_info(),
                        "storage": self.engine.store_info(),
                    },
                )
            if method == "GET" and path == "/ready":
                try:
                    storage_readiness = self.engine.readiness_info()
                except Exception:
                    return self._respond(
                        start_response,
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "status": "not_ready",
                            "service": "ece329-lab-design-workflow",
                            "error": "storage_unavailable",
                        },
                    )
                return self._respond(
                    start_response,
                    HTTPStatus.OK,
                    {
                        "status": "ready",
                        "service": "ece329-lab-design-workflow",
                        "storage": storage_readiness,
                        "generator": self.engine.generator_info(),
                    },
                )
            if method == "GET" and path == "/v1/stages":
                return self._respond(start_response, HTTPStatus.OK, {"stages": self.engine.list_stages()})
            if method == "GET" and path == "/v1/knowledge/source":
                return self._respond(start_response, HTTPStatus.OK, self.engine.knowledge_source())
            if method == "GET" and path == "/v1/knowledge/concepts":
                return self._respond(start_response, HTTPStatus.OK, {"concepts": self.engine.list_knowledge_concepts()})
            if method == "GET" and path == "/v1/knowledge/supplemental-concepts":
                return self._respond(
                    start_response,
                    HTTPStatus.OK,
                    {"concepts": self.engine.list_supplemental_concepts()},
                )
            if method == "GET" and path == "/v1/knowledge/formulas":
                return self._respond(start_response, HTTPStatus.OK, {"formulas": self.engine.list_knowledge_formulas()})
            if method == "GET" and path == "/v1/knowledge/search":
                query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0].strip()
                if not query:
                    raise ValueError("q must not be empty")
                if len(query) > self.settings.max_text_chars:
                    raise ValueError(
                        f"q must not exceed {self.settings.max_text_chars} characters"
                    )
                return self._respond(start_response, HTTPStatus.OK, self.engine.search_knowledge(query))
            if method == "POST" and path == "/v1/designs":
                self._require_admission_code(environ)
                body = self._read_json(environ)
                idea = self._required_string(body, "idea", self.settings.max_text_chars)
                interaction_state = self._optional_string(body, "interaction_state")
                idempotency_key = self._idempotency_key(environ)
                fingerprint = self._payload_fingerprint(
                    {"idea": idea, "interaction_state": interaction_state}
                )
                if not idempotency_key:
                    result = self.engine.create_design(idea, interaction_state)
                else:
                    with self._create_idempotency_lock:
                        request_lock = self._create_request_locks.setdefault(
                            idempotency_key, RLock()
                        )
                    with request_lock:
                        with self._create_idempotency_lock:
                            cached = self._create_idempotency.get(idempotency_key)
                        if cached is not None:
                            if cached.get("fingerprint") != fingerprint:
                                raise WorkflowError(
                                    "The same Idempotency-Key cannot be reused for a different design."
                                )
                            result = deepcopy(cached["response"])
                        else:
                            result = self.engine.create_design(idea, interaction_state)
                            with self._create_idempotency_lock:
                                self._create_idempotency[idempotency_key] = {
                                    "fingerprint": fingerprint,
                                    "response": deepcopy(result),
                                }
                                while len(self._create_idempotency) > 100:
                                    oldest = next(iter(self._create_idempotency))
                                    self._create_idempotency.pop(oldest, None)
                                    self._create_request_locks.pop(oldest, None)
                return self._respond(start_response, HTTPStatus.CREATED, result)

            resume_match = re.fullmatch(r"/v1/designs/([^/]+)/resume", path)
            if method == "POST" and resume_match:
                body = self._read_json(environ)
                resume_token = self._required_string(body, "resume_token", 256)
                result = self.engine.resume_design(resume_match.group(1), resume_token)
                self._refresh_cached_create_response(result)
                return self._respond(start_response, HTTPStatus.OK, result)

            report_match = re.fullmatch(r"/v1/designs/([^/]+)/report\.pdf", path)
            if method == "GET" and report_match:
                design_id = report_match.group(1)
                self._require_design_token(environ, design_id)
                body = self.engine.render_report_pdf(design_id)
                safe_name = f"ece329-emvr-{design_id}.pdf"
                return self._respond_bytes(
                    start_response,
                    HTTPStatus.OK,
                    body,
                    [
                        ("Content-Type", "application/pdf"),
                        ("Content-Disposition", f'attachment; filename="{safe_name}"'),
                    ],
                )

            guided_export_match = re.fullmatch(
                r"/v1/designs/([^/]+)/guided-summary\.txt", path
            )
            if method == "GET" and guided_export_match:
                design_id = guided_export_match.group(1)
                self._require_design_token(environ, design_id)
                body = self.engine.render_guided_summary_text(design_id)
                safe_name = f"ece329-guided-summary-{design_id}.txt"
                return self._respond_bytes(
                    start_response,
                    HTTPStatus.OK,
                    body,
                    [
                        ("Content-Type", "text/plain; charset=utf-8"),
                        ("Content-Disposition", f'attachment; filename="{safe_name}"'),
                    ],
                )

            design_match = re.fullmatch(r"/v1/designs/([^/]+)", path)
            if method == "GET" and design_match:
                self._require_design_token(environ, design_match.group(1))
                query = parse_qs(environ.get("QUERY_STRING", ""))
                include_history = query.get("include_history", ["false"])[0].lower() == "true"
                result = self.engine.get_design(design_match.group(1), include_history=include_history)
                return self._respond(start_response, HTTPStatus.OK, result)
            if method == "DELETE" and design_match:
                self._require_design_token(environ, design_match.group(1))
                self.engine.delete_design(design_match.group(1))
                return self._respond(start_response, HTTPStatus.NO_CONTENT, None)

            turn_match = re.fullmatch(r"/v1/designs/([^/]+)/turns", path)
            if method == "POST" and turn_match:
                self._require_design_token(environ, turn_match.group(1))
                body = self._read_json(environ)
                self._required_string(body, "message", self.settings.max_text_chars)
                header_turn_id = self._idempotency_key(environ)
                body_turn_id = body.get("turn_id")
                if header_turn_id and body_turn_id and header_turn_id != body_turn_id:
                    raise ValueError("turn_id and Idempotency-Key must match")
                if header_turn_id and not body_turn_id:
                    body["turn_id"] = header_turn_id
                result = self.engine.process_turn(turn_match.group(1), body)
                return self._respond(start_response, HTTPStatus.OK, result)

            prompt_match = re.fullmatch(r"/v1/designs/([^/]+)/prompt", path)
            if method == "POST" and prompt_match:
                if not self.settings.prompt_debug_enabled:
                    return self._respond(
                        start_response,
                        HTTPStatus.NOT_FOUND,
                        {"error": "route_not_found"},
                    )
                self._require_design_token(environ, prompt_match.group(1))
                self._require_prompt_debug_token(environ)
                body = self._read_json(environ)
                message = self._optional_string(body, "message") or ""
                if len(message) > self.settings.max_text_chars:
                    raise ValueError(
                        f"message must not exceed {self.settings.max_text_chars} characters"
                    )
                result = self.engine.get_prompt_packet(prompt_match.group(1), message)
                return self._respond(start_response, HTTPStatus.OK, result)

            return self._respond(start_response, HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
        except SessionNotFound as exc:
            return self._respond(start_response, HTTPStatus.NOT_FOUND, {"error": "session_not_found", "detail": str(exc)})
        except DesignAccessDenied as exc:
            return self._respond(start_response, HTTPStatus.UNAUTHORIZED, {"error": "access_denied", "detail": str(exc)})
        except RequestTooLarge as exc:
            return self._respond(start_response, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large", "detail": str(exc)})
        except json.JSONDecodeError as exc:
            return self._respond(start_response, HTTPStatus.BAD_REQUEST, {"error": "invalid_json", "detail": str(exc)})
        except (ValueError, UnicodeDecodeError, StageCompletionError) as exc:
            return self._respond(start_response, HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "detail": str(exc)})
        except ModelServiceError as exc:
            return self._respond(start_response, HTTPStatus.BAD_GATEWAY, {"error": "model_service_error", "detail": str(exc)})
        except WorkflowError as exc:
            return self._respond(start_response, HTTPStatus.CONFLICT, {"error": "workflow_error", "detail": str(exc)})

    def _read_json(self, environ: dict[str, Any]) -> dict[str, Any]:
        raw_length = environ.get("CONTENT_LENGTH") or "0"
        length = int(raw_length)
        if length < 0:
            raise ValueError("CONTENT_LENGTH must not be negative")
        if length > self.settings.max_body_bytes:
            raise RequestTooLarge(
                f"request body exceeds {self.settings.max_body_bytes} bytes"
            )
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _client_key(self, environ: dict[str, Any]) -> str:
        if self.settings.trust_proxy:
            forwarded = str(environ.get("HTTP_X_FORWARDED_FOR", ""))
            if forwarded:
                return forwarded.split(",", 1)[0].strip() or "unknown"
        return str(environ.get("REMOTE_ADDR", "unknown")) or "unknown"

    @staticmethod
    def _idempotency_key(environ: dict[str, Any]) -> str | None:
        value = str(environ.get("HTTP_IDEMPOTENCY_KEY", "")).strip()
        if not value:
            return None
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value) is None:
            raise ValueError("Idempotency-Key must be 8-128 URL-safe characters")
        return value

    @staticmethod
    def _payload_fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _refresh_cached_create_response(self, resumed: dict[str, Any]) -> None:
        """Refresh delayed create retries after design credentials rotate."""

        design_id = str(resumed.get("design_id") or "")
        if not design_id:
            return
        with self._create_idempotency_lock:
            for cached in self._create_idempotency.values():
                response = cached.get("response") if isinstance(cached, dict) else None
                if not isinstance(response, dict) or response.get("design_id") != design_id:
                    continue
                refreshed = deepcopy(response)
                for field in (
                    "design_access_token",
                    "design_resume_token",
                    "revision",
                    "current_stage",
                    "interaction_state",
                    "quality_review",
                    "task_report",
                    "report_ready",
                    "report_url",
                    "guided_export_ready",
                    "guided_export_url",
                ):
                    if field in resumed:
                        refreshed[field] = deepcopy(resumed[field])
                if "status" in resumed:
                    refreshed["workflow_status"] = deepcopy(resumed["status"])
                cached["response"] = refreshed

    def _require_admission_code(self, environ: dict[str, Any]) -> None:
        candidate = str(environ.get("HTTP_X_ECE329_ACCESS_CODE", ""))
        if not self.settings.accepts_access_code(candidate):
            raise DesignAccessDenied("A valid course access code is required to create a design.")

    def _require_design_token(self, environ: dict[str, Any], design_id: str) -> None:
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not self.engine.verify_design_token(design_id, token.strip()):
            raise DesignAccessDenied("A valid design access token is required.")

    def _require_prompt_debug_token(self, environ: dict[str, Any]) -> None:
        candidate = str(environ.get("HTTP_X_ECE329_DEBUG_TOKEN", ""))
        if not self.settings.accepts_prompt_debug_token(candidate):
            raise DesignAccessDenied("A valid prompt debug token is required.")

    @staticmethod
    def _required_string(
        body: dict[str, Any],
        field: str,
        max_chars: int | None = None,
    ) -> str:
        value = body.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        if max_chars is not None and len(normalized) > max_chars:
            raise ValueError(f"{field} must not exceed {max_chars} characters")
        return normalized

    @staticmethod
    def _optional_string(body: dict[str, Any], field: str) -> str | None:
        value = body.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string or null")
        return value

    def _cors_start_response(
        self,
        start_response: Callable[[str, list[tuple[str, str]]], Any],
        origin: str,
    ) -> Callable[[str, list[tuple[str, str]]], Any]:
        def wrapped(status: str, headers: list[tuple[str, str]]) -> Any:
            cors_headers = list(headers)
            if origin and self.settings.allows_origin(origin):
                allowed_origin = "*" if "*" in self.settings.allowed_origins else origin
                cors_headers.extend(
                    [
                        ("Access-Control-Allow-Origin", allowed_origin),
                        ("Access-Control-Allow-Headers", "Content-Type, Authorization, Idempotency-Key, X-ECE329-Access-Code, X-ECE329-Debug-Token"),
                        ("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS"),
                        ("Vary", "Origin"),
                    ]
                )
            return start_response(status, cors_headers)

        return wrapped

    @staticmethod
    def _respond(
        start_response: Callable[[str, list[tuple[str, str]]], Any],
        status: HTTPStatus,
        payload: dict[str, Any] | None,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        headers = JsonHeaders + list(extra_headers or [])
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.append(("Content-Length", str(len(body))))
        start_response(f"{status.value} {status.phrase}", headers)
        return [body]

    @staticmethod
    def _respond_bytes(
        start_response: Callable[[str, list[tuple[str, str]]], Any],
        status: HTTPStatus,
        body: bytes,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        headers = [
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            *list(extra_headers or []),
            ("Content-Length", str(len(body))),
        ]
        start_response(f"{status.value} {status.phrase}", headers)
        return [body]


application = WorkflowAPI()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ECE329 workflow API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    with make_server(args.host, args.port, application) as server:
        print(f"ECE329 workflow API listening on http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
