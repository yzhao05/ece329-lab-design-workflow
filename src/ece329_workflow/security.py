from __future__ import annotations

import os
import hmac
import time
from dataclasses import dataclass
from threading import RLock
from typing import Mapping
from urllib.parse import urlsplit


DEFAULT_LOCAL_ORIGINS = (
    "http://127.0.0.1:4173",
    "http://localhost:4173",
)


def _positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return normalized == "true"


def _normalize_origin(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized == "*":
        return normalized
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "ECE329_ALLOWED_ORIGINS entries must be HTTP(S) origins without paths"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("ECE329_ALLOWED_ORIGINS contains an invalid port") from exc
    return normalized


@dataclass(frozen=True, slots=True)
class APISettings:
    allowed_origins: tuple[str, ...] = DEFAULT_LOCAL_ORIGINS
    max_body_bytes: int = 65_536
    max_text_chars: int = 4_000
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    trust_proxy: bool = False
    access_code: str = ""
    prompt_debug_enabled: bool = False
    prompt_debug_token: str = ""

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "APISettings":
        env = os.environ if environ is None else environ
        raw_origins = env.get("ECE329_ALLOWED_ORIGINS", "").strip()
        if raw_origins:
            origins = tuple(
                dict.fromkeys(
                    _normalize_origin(origin)
                    for origin in raw_origins.split(",")
                    if origin.strip()
                )
            )
        else:
            origins = DEFAULT_LOCAL_ORIGINS
        prompt_debug_enabled = _boolean(
            env.get("ECE329_ENABLE_PROMPT_DEBUG", "false"),
            "ECE329_ENABLE_PROMPT_DEBUG",
        )
        prompt_debug_token = env.get("ECE329_PROMPT_DEBUG_TOKEN", "").strip()
        if prompt_debug_enabled and not prompt_debug_token:
            raise ValueError(
                "ECE329_PROMPT_DEBUG_TOKEN is required when prompt debug is enabled"
            )
        return cls(
            allowed_origins=origins,
            max_body_bytes=_positive_int(
                env.get("ECE329_MAX_BODY_BYTES", "65536"),
                "ECE329_MAX_BODY_BYTES",
            ),
            max_text_chars=_positive_int(
                env.get("ECE329_MAX_TEXT_CHARS", "4000"),
                "ECE329_MAX_TEXT_CHARS",
            ),
            rate_limit_requests=_positive_int(
                env.get("ECE329_RATE_LIMIT_REQUESTS", "30"),
                "ECE329_RATE_LIMIT_REQUESTS",
            ),
            rate_limit_window_seconds=_positive_int(
                env.get("ECE329_RATE_LIMIT_WINDOW_SECONDS", "60"),
                "ECE329_RATE_LIMIT_WINDOW_SECONDS",
            ),
            trust_proxy=_boolean(
                env.get("ECE329_TRUST_PROXY", "false"),
                "ECE329_TRUST_PROXY",
            ),
            access_code=env.get("ECE329_ACCESS_CODE", "").strip(),
            prompt_debug_enabled=prompt_debug_enabled,
            prompt_debug_token=prompt_debug_token,
        )

    def allows_origin(self, origin: str) -> bool:
        normalized = origin.strip().rstrip("/")
        return not normalized or "*" in self.allowed_origins or normalized in self.allowed_origins

    def accepts_access_code(self, candidate: str) -> bool:
        return not self.access_code or hmac.compare_digest(self.access_code, candidate)

    def accepts_prompt_debug_token(self, candidate: str) -> bool:
        return (
            self.prompt_debug_enabled
            and bool(self.prompt_debug_token)
            and hmac.compare_digest(self.prompt_debug_token, candidate)
        )


class FixedWindowRateLimiter:
    def __init__(self, request_limit: int, window_seconds: int) -> None:
        self.request_limit = request_limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = RLock()

    def allow(self, client_key: str, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        with self._lock:
            started, count = self._buckets.get(client_key, (current, 0))
            elapsed = current - started
            if elapsed >= self.window_seconds:
                started, count = current, 0
                elapsed = 0
            if count >= self.request_limit:
                retry_after = max(1, int(self.window_seconds - elapsed + 0.999))
                return False, retry_after
            self._buckets[client_key] = (started, count + 1)
            if len(self._buckets) > 10_000:
                self._discard_expired(current)
            return True, 0

    def _discard_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._buckets = {
            key: value
            for key, value in self._buckets.items()
            if value[0] > cutoff
        }
