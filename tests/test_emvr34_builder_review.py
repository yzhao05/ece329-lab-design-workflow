"""Magnetic handoff completeness and shared workflow regression checks."""
from copy import deepcopy
import json
from io import BytesIO

import pytest

from ece329_workflow.builder_defaults import (
    build_implementation_defaults, implementation_defaults_approval_valid,
    measurement_disables_chart, measurement_is_qualitative_only,
)
from ece329_workflow.builder_input import (
    _builder_formula_contracts, _student_task_contracts, build_builder_gate1_input,
    validate_builder_gate1_input,
    render_builder_gate1_input_pdf,
)
from ece329_workflow.builder_requirements import missing_builder_requirements
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.models import Stage
from ece329_workflow.physics_blueprint import FORMULA_IMPLEMENTATION_NOTES
from ece329_workflow.knowledge_base import KNOWLEDGE
from tools.export_emvr34_regression import VALUES, completed_magnetic_engine


@pytest.fixture(scope="module")
def magnetic_design():
    return completed_magnetic_engine()[1]


def test_magnetic_workflow_exports_exact_selected_physics_and_confirmed_steps(magnetic_design):
    payload = build_builder_gate1_input(magnetic_design)
    validate_builder_gate1_input(payload)
    assert [f["formula_id"] for f in payload["physics"]["formulas"]] == ["long_solenoid_field", "linear_magnetic_medium"]
    assert [row["goal"] for row in payload["student_tasks"]] == VALUES["procedure_steps"]
    assert payload["physics"]["numerical_model"] == VALUES["numerical_model_specifications"]
    text = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("coulomb_point_charge", "点电荷", "E_total", "定性空间比较：固定视角", "unresolved"):
        assert forbidden not in text
    for required in ("0.00125663706212", "1e-9 T", "右手SI", "Build Scene", "EmVrLabContractRunner", "无需迭代"):
        assert required in text
    assert not missing_builder_requirements(magnetic_design)


def test_no_chart_does_not_discard_quantitative_magnetic_measurements(magnetic_design):
    measurement = VALUES["measurement_specifications"]
    assert measurement_disables_chart(measurement)
    assert not measurement_is_qualitative_only(measurement)
    defaults = build_implementation_defaults(magnetic_design)
    assert "保留数值读数" in defaults["measurement_and_chart_policy"]
    assert "定性比较" not in defaults["measurement_and_chart_policy"]


def test_restore_and_reset_do_not_turn_snapshot_mentions_into_capture():
    tasks = _student_task_contracts([
        "执行Restore，核对最近快照的电流和读数。",
        "执行Reset，核对快照清空；Back回到Start。",
    ], "中心Bz，单位T")
    assert tasks[0]["exit_state"] == "VALID"
    assert "without adding a snapshot" in tasks[0]["unity_response"]
    assert tasks[1]["exit_state"] == "START"
    assert all("Capture" not in t["expected_action"] for t in tasks)


def test_set_value_capture_compare_keeps_all_three_actions_in_order():
    row = _student_task_contracts(["电流设为2 A，Capture保存读数，然后并排比较基准"], "Bz T")[0]
    action = row["expected_action"].split("Unity操作映射：", 1)[1]
    assert action.index("control") < action.index("Capture") < action.index("compare")
    assert row["exit_state"] == "COMPARING"


def test_clearing_or_inspecting_snapshots_is_not_a_capture_action():
    for text in ("检查保存的快照", "清除快照", "Reset后不要Capture"):
        row = _student_task_contracts([text], "Bz T")[0]
        assert row["exit_state"] != "CAPTURED"


def test_unknown_formula_reference_is_rejected_instead_of_silently_omitted():
    with pytest.raises(ValueError, match="unknown.*missing_formula"):
        _builder_formula_contracts({"primary_formula_ids": ["long_solenoid_field", "missing_formula"]}, {})


@pytest.mark.parametrize("defect", ["physics_copy", "duplicate_role"])
def test_formula_contract_reference_disconnect_blocks_export(magnetic_design, defect):
    payload = build_builder_gate1_input(magnetic_design)
    if defect == "physics_copy":
        payload["physics"]["formulas"] = []
        error = "disconnected"
    else:
        payload["formula_driven_experiment"]["formula_contracts"].append(deepcopy(payload["physics"]["formulas"][0]))
        error = "unique"
    with pytest.raises(ValueError, match=error):
        validate_builder_gate1_input(payload)


def test_missing_selected_method_cannot_be_exported_as_legacy(magnetic_design):
    session = deepcopy(magnetic_design)
    session.design_context["emvr_design"]["formula_flow"]["experiment_methods"] = []
    with pytest.raises(ValueError, match="method references are unknown"):
        build_builder_gate1_input(session)


def test_new_physics_guidance_has_no_unknown_formula_ids():
    assert set(FORMULA_IMPLEMENTATION_NOTES) <= {f["id"] for f in KNOWLEDGE.formulas}


def test_new_numerical_contract_requires_renewed_implementation_review(magnetic_design):
    session = deepcopy(magnetic_design)
    assert implementation_defaults_approval_valid(session)
    apply_stage_field_updates(session, [{"field": "numerical_model_specifications", "operation": "REPLACE",
        "value": VALUES["numerical_model_specifications"].replace("1e-9 T", "1e-10 T")}], stage=Stage.EXPECTED_DATA_VISUALIZATION)
    assert not implementation_defaults_approval_valid(session)
    assert "implementation_defaults" in {item["field"] for item in missing_builder_requirements(session)}


def test_procedure_list_replacement_preserves_step_boundaries_and_pdf_tail(magnetic_design):
    session = deepcopy(magnetic_design)
    steps = [*VALUES["procedure_steps"], "重新Start验证新一轮不会接收旧计算结果。"]
    apply_stage_field_updates(session, [{"field": "procedure_steps", "operation": "REPLACE", "value": steps}],
                              stage=Stage.CONCEPTUAL_PROCEDURE)
    assert session.design_context["emvr_design"]["field_state"]["procedure_steps"] == steps
    assert "S6 重新Start" in build_implementation_defaults(session)["experiment_flow"]


def test_actual_magnetic_pdf_retains_values_steps_and_construction_contract(magnetic_design):
    pdfplumber = pytest.importorskip("pdfplumber")
    with pdfplumber.open(BytesIO(render_builder_gate1_input_pdf(magnetic_design))) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    compact = "".join(text.split())
    for required in ("long_solenoid_field", "linear_magnetic_medium", "0.00125663706212", "student_tasks[S5]",
                     "EmVrLabContractRunner", "BuildScene", "物理坐标", "无自由探针", "1e-9T"):
        assert required in compact
    for forbidden in ("coulomb_point_charge", "student_tasks[S6]", "E_total", "点电荷", "unresolved", "E:\\"):
        assert forbidden not in text
