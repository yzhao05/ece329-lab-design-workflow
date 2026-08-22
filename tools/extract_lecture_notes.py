"""Extract a page-indexed text snapshot from the authorized ECE329 PDF.

This utility is intentionally separate from the runtime workflow. It is used
to refresh the curated knowledge files when the lecture-note PDF changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path

from pypdf import PdfReader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    source_bytes = args.input_pdf.read_bytes()
    reader = PdfReader(args.input_pdf)
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        pages.append(
            {
                "page": page_number,
                "text": page.extract_text() or "",
            }
        )
    payload = {
        "source_filename": args.input_pdf.name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "page_count": len(pages),
        "pages": pages,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
