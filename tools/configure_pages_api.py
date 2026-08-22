from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "docs" / "assets" / "config.js"


def normalize_https_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("API base URL must not contain whitespace or control characters")
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("API base URL must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("API base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("API base URL must not contain a query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("API base URL contains an invalid port") from exc
    return normalized


def configure_api_url(config_path: Path, api_base_url: str) -> None:
    normalized = normalize_https_url(api_base_url)
    source = config_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(?m)^(\s*)API_BASE_URL:\s*"[^"]*"',
        lambda match: f"{match.group(1)}API_BASE_URL: {json.dumps(normalized)}",
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Unable to find exactly one API_BASE_URL setting")
    config_path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject the public backend URL into the Pages artifact")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    configure_api_url(args.config, args.api_base_url)


if __name__ == "__main__":
    main()
