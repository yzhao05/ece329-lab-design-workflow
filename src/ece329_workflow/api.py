from __future__ import annotations

import argparse
import json
import re
from http import HTTPStatus
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
            if method == "GET" and path == "/v1/stages":
                return self._respond(start_response, HTTPStatus.OK, {"stages": self.engine.list_stages()})
            if method == "GET" and path == "/v1/knowledge/source":
                return self._respond(start_response, HTTPStatus.OK, self.engine.knowledge_source())
            if method == "GET" and path == "/v1/knowledge/concepts":
                return self._respond(start_response, HTTPStatus.OK, {"concepts": self.engine.list_knowledge_concepts()})
            if method == "GET" and path == "/v1/knowledge/formulas":
                return self._respond(start_response, HTTPStatus.OK, {"formulas": self.engine.list_knowledge_formulas()})
            if method == "GET" and path == "/v1/knowledge/search":
                query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0].strip()
                if not query:
                    raise ValueError("q must not be empty")
                return self._respond(start_response, HTTPStatus.OK, self.engine.search_knowledge(query))
            if method == "POST" and path == "/v1/designs":
                self._require_admission_code(environ)
                body = self._read_json(environ)
                idea = self._required_string(body, "idea", self.settings.max_text_chars)
                interaction_state = self._optional_string(body, "interaction_state")
                result = self.engine.create_design(idea, interaction_state)
                return self._respond(start_response, HTTPStatus.CREATED, result)

            design_match = re.fullmatch(r"/v1/designs/([^/]+)", path)
            if method == "GET" and design_match:
                self._require_design_token(environ, design_match.group(1))
                query = parse_qs(environ.get("QUERY_STRING", ""))
                include_history = query.get("include_history", ["false"])[0].lower() == "true"
                result = self.engine.get_design(design_match.group(1), include_history=include_history)
                return self._respond(start_response, HTTPStatus.OK, result)

            turn_match = re.fullmatch(r"/v1/designs/([^/]+)/turns", path)
            if method == "POST" and turn_match:
                self._require_design_token(environ, turn_match.group(1))
                body = self._read_json(environ)
                self._required_string(body, "message", self.settings.max_text_chars)
                result = self.engine.process_turn(turn_match.group(1), body)
                return self._respond(start_response, HTTPStatus.OK, result)

            prompt_match = re.fullmatch(r"/v1/designs/([^/]+)/prompt", path)
            if method == "POST" and prompt_match:
                self._require_design_token(environ, prompt_match.group(1))
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

    def _require_admission_code(self, environ: dict[str, Any]) -> None:
        candidate = str(environ.get("HTTP_X_ECE329_ACCESS_CODE", ""))
        if not self.settings.accepts_access_code(candidate):
            raise DesignAccessDenied("A valid course access code is required to create a design.")

    def _require_design_token(self, environ: dict[str, Any], design_id: str) -> None:
        authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not self.engine.verify_design_token(design_id, token.strip()):
            raise DesignAccessDenied("A valid design access token is required.")

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
                        ("Access-Control-Allow-Headers", "Content-Type, Authorization, X-ECE329-Access-Code"),
                        ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
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
