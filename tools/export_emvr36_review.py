"""Reproduce the audited synthetic Builder handoff using production export."""
import json
from pathlib import Path

from ece329_workflow.builder_input import build_builder_gate1_input
from tools.export_emvr35_review import MU, RADIUS, circle_field, completed_ring_engine


def main():
    for z in (0.0, 0.5, 1.0):
        actual = circle_field(z)
        expected = MU * RADIUS**2 / (2 * (RADIUS**2 + z*z)**1.5)
        assert abs(actual[2] - expected) < 1e-9
        assert max(abs(actual[0]), abs(actual[1])) < 1e-12
    engine, session = completed_ring_engine()
    output = Path('output/pdf/emvr36-biot-savart-builder-review.pdf')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(engine.render_builder_input_pdf(session.design_id))
    scratch = Path('build/emvr36-review')
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / 'builder-input.json').write_text(
        json.dumps(build_builder_gate1_input(session), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{output}: synthetic handoff and three axial checks passed; no Unity execution.')


if __name__ == '__main__':
    main()
