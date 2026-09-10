"""Render synthetic emvr33 regression PDFs, without a live model or Unity.

This exercises the completed workflow with late no-probe/qualitative revisions.
It does not recreate or approve the user's historical session.
Run from repo root with PYTHONPATH=src: python -m tools.export_emvr33_regression
"""
from copy import deepcopy
import json
from pathlib import Path

from ece329_workflow.builder_defaults import (
    build_implementation_defaults, format_implementation_defaults, record_implementation_defaults_approval,
)
from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import InteractionState, Stage
from ece329_workflow.reporting import build_emvr_task_report
from tests.test_engine import continue_emvr


def main():
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    result = engine.create_design("请用EMVR设计双点电荷电场叠加实验", interaction_state=InteractionState.EMVR_DIRECT)
    for _ in range(70):
        if result["workflow_status"] == "complete":
            break
        result = continue_emvr(engine, result)
    else:
        raise RuntimeError("Synthetic workflow did not complete")
    session = engine.store.get(result["design_id"])
    old_outputs = deepcopy(session.stage_outputs)
    measurement = (
        "距离：计算公式d=|r1-r2|，单位m，采样当前电荷位置，拖动时刷新。\n"
        "电荷类型：由Q1、Q2符号比较确定同号或异号，单位无量纲，切换时刷新。\n"
        "空间形态：定性描述，不涉及数值计算；以同一视角和场线种子比较连接与弯曲形态。\n"
        "空间探针不适用：本实验不设置可移动空间探针，研究全局空间形态。\n"
        "不生成定量曲线；模型更新后刷新场图、文字判据及比较快照。"
    )
    updates = {
        "measurement_specifications": measurement,
        "initial_reset_state": "Initial两电荷在原点两侧，间距2.0 m、异号；面板和场线按默认参数显示。Reset恢复该配置并清除比较记录。",
        "visualization_plan": "同一距离下并排比较同号与异号场线，保持种子数、色标和观察视角一致，记录差异。",
    }
    apply_stage_field_updates(session, [
        {"field": field, "operation": "REPLACE", "value": value}
        for field, value in updates.items()
    ], stage=Stage.EXPECTED_DATA_VISUALIZATION, provenance="SYNTHETIC_REGRESSION")
    # Deliberately retain earlier stage outputs and contaminated audit entries.
    session.stage_outputs = old_outputs
    session.design_context["emvr_design"].setdefault("stage_inputs", {})[Stage.EXPECTED_DATA_VISUALIZATION.value] = [
        {"content": "采用"}, {"content": "场景首次打开时探针位于原点；Reset清除曲线。"},
        {"content": "算法：RK4，最大500步。"},
    ]
    contract = format_implementation_defaults(build_implementation_defaults(session))
    apply_stage_field_updates(session, [{"field": "implementation_defaults", "operation": "REPLACE", "value": contract}],
                              stage=Stage.DESIGN_VALUE_AND_LIMITATIONS, provenance="SYNTHETIC_REGRESSION")
    record_implementation_defaults_approval(session, source="SYNTHETIC_REGRESSION")
    engine.store.save(session)
    handoff = build_builder_gate1_input(session)
    display_row = next(row for row in handoff["visualization"] if row["key"] == "visualization.requirements")
    assert display_row["value"] == updates["visualization_plan"]
    out = Path("build/emvr33-regression")
    out.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("student-report.pdf", engine.render_report_pdf(session.design_id)),
        ("builder-input.pdf", engine.render_builder_input_pdf(session.design_id)),
    ):
        (out / name).write_bytes(content)
        print(f"{name}: {len(content)} bytes")
    (out / "report.json").write_text(json.dumps(build_emvr_task_report(session), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Synthetic regression artifacts only; no live session or Unity was changed.")


if __name__ == "__main__":
    main()
