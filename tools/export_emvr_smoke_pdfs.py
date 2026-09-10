"""Export deterministic, synthetic EMVR PDFs for layout QA (no live model).

Run from the repository root: python -m tools.export_emvr_smoke_pdfs
The fixtures exercise workflow/export plumbing, not experimental correctness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import InteractionState
from tests.test_engine import continue_emvr


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("build/emvr-pdf-qa"))
    args = parser.parse_args()
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    result = engine.create_design(
        "请用EMVR设计双点电荷电场叠加实验",
        interaction_state=InteractionState.EMVR_DIRECT,
    )
    for turn in range(70):
        if result["workflow_status"] == "complete":
            break
        result = continue_emvr(engine, result)
    else:
        raise RuntimeError("EMVR did not complete within 70 turns")
    session = engine.store.get(result["design_id"])
    payload = build_builder_gate1_input(session)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("emvr-design-smoke.pdf", engine.render_report_pdf(session.design_id)),
        ("emvr-builder-smoke.pdf", engine.render_builder_input_pdf(session.design_id)),
    ):
        target = args.output_dir / name
        target.write_bytes(content)
        print(f"{target.resolve()}: {len(content)} bytes")
    audit = args.output_dir / "builder-contract-smoke.json"
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Completed in {turn} turns; synthetic fixtures only, no Unity execution.")


if __name__ == "__main__":
    main()
