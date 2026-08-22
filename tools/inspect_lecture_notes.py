"""Print structural and formula candidates from an extracted page index."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("page_index", type=Path)
    parser.add_argument(
        "--mode", choices=("headings", "equations", "pages"), default="headings"
    )
    parser.add_argument("--page", type=int, action="append", default=[])
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    args = parser.parse_args()
    data = json.loads(args.page_index.read_text(encoding="utf-8"))

    if args.mode == "headings":
        for page in data["pages"]:
            text = " ".join(page["text"].split())
            if "Copyright" in text[:500]:
                print(f"{page['page']:3}: {text[:300]}")
        return
    if args.mode == "equations":
        markers = ("=", "∇", "∫", "∮", "×", "∂", "Γ", "λ", "ω")
        for page in data["pages"]:
            if args.start is not None and page["page"] < args.start:
                continue
            if args.end is not None and page["page"] > args.end:
                continue
            lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
            candidates = [line for line in lines if any(mark in line for mark in markers)]
            if candidates:
                print(f"--- PAGE {page['page']} ---")
                print("\n".join(candidates))
        return
    selected = set(args.page)
    for page in data["pages"]:
        if page["page"] in selected:
            print(f"--- PAGE {page['page']} ---")
            print(page["text"])


if __name__ == "__main__":
    main()
