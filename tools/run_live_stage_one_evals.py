from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.openai_generator import generator_from_environment
from ece329_workflow.store import InMemorySessionStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage_one_dialogues.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run paid Stage 1 boundary evaluations against the configured OpenAI model."
    )
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="Confirm that this command may create billable OpenAI API requests.",
    )
    args = parser.parse_args()
    if not args.confirm_cost:
        parser.error("--confirm-cost is required")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        parser.error("OPENAI_API_KEY is required")

    model_env = dict(os.environ)
    model_env["ECE329_GENERATOR"] = "openai"
    model_env["ECE329_OPENAI_FALLBACK"] = "false"
    generator = generator_from_environment(model_env)
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []

    for case in cases:
        engine = WorkflowEngine(
            generator=generator,
            store=InMemorySessionStore(session_ttl_days=1),
        )
        first = engine.create_design(case["initial"])
        result = first
        if case.get("followup"):
            selected = first["stage_payload"]["alternative_ideas"][
                case["selected_index"]
            ]
            turn = {"message": case["followup"]}
            if case.get("send_option_id"):
                turn["selected_option_id"] = selected["option_id"]
            result = engine.process_turn(first["design_id"], turn)

        actual_category = result["stage_payload"].get("input_category")
        expected_category = case["expected_category"]
        actual_mode = result["interaction_state"]
        expected_mode = case.get("expected_mode")
        passed = actual_category == expected_category and (
            expected_mode is None or actual_mode == expected_mode
        )
        print(
            f"{'PASS' if passed else 'FAIL'} {case['id']}: "
            f"category={actual_category}, mode={actual_mode}"
        )
        if not passed:
            failures.append(case["id"])

    print(f"\n{len(cases) - len(failures)}/{len(cases)} live evaluations passed.")
    if failures:
        print("Failed cases: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
