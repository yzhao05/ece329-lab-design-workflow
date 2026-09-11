"""Export synthetic student/Builder handoffs through the current workflow.

No live model or Unity execution; review approval belongs only to the fixture.
"""
import json
from pathlib import Path

from ece329_workflow.builder_input import build_builder_gate1_input, validate_builder_gate1_input
from tools.export_emvr37_review import reviewed_engine, axial_checks


def main():
    engine, session = reviewed_engine()
    payload = build_builder_gate1_input(session)
    validate_builder_gate1_input(payload)
    output = Path('output/pdf')
    output.mkdir(parents=True, exist_ok=True)
    for label, render in [('builder', engine.render_builder_input_pdf), ('student', engine.render_report_pdf)]:
        path = output / f'review-20260912-{label}.pdf'
        path.write_bytes(render(session.design_id))
        print(path)
    scratch = Path('build/review-20260912')
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / 'builder.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    checks = axial_checks()
    (scratch / 'numeric-checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    print(f'{len(checks)} independent axial checks passed. Synthetic review only; no Unity execution.')


if __name__ == '__main__':
    main()
