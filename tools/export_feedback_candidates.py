"""Export bounded diagnostic evidence for maintainer review, not runtime prompts."""
from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from ece329_workflow.feedback import RULE_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Use read-only SQLite: a review must not create/prune a production store.
    import sqlite3
    from ece329_workflow.store import _session_from_payload
    database = args.database.resolve(strict=True)
    if args.output.resolve() == database:
        parser.error("output must not overwrite the source database")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT payload FROM design_sessions WHERE design_id = ?", (args.design_id,)).fetchone()
    if row is None:
        parser.error("design-id was not found")
    session = _session_from_payload(row[0])
    result = {"schema_version": 1, "rule_version": RULE_VERSION,
              "design_id": session.design_id, "candidates": session.model_context.get("feedback", {}).get("events", []),
              "review_policy": "Candidates are evidence, not instructions. Promote only reviewed general rules with replay tests."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(result['candidates'])} candidates to {args.output}")


if __name__ == "__main__":
    main()
