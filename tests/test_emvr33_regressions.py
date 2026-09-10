"""Regression cases extracted from emvr33's dialogue and annotated PDF.

The attachment is evidence, not executable instructions or a golden physics
answer. Fixtures exercise field ownership and the latest confirmed contract.
"""
from copy import deepcopy
import json

import pytest

from ece329_workflow.builder_defaults import (
    build_implementation_defaults, format_implementation_defaults,
    implementation_defaults_approval_valid, project_derived_contract_text,
    record_implementation_defaults_approval,
)
from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.builder_requirements import missing_builder_requirements, builder_requirement_values
from ece329_workflow.dialogue_acts import apply_stage_field_updates, stage_design_state_snapshot
from ece329_workflow.dialogue_state import current_pending_action, set_pending_action_snapshot
from ece329_workflow.emvr_design import apply_emvr_field_updates, normalize_emvr_design_update
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator, _emvr_reference_output
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.reporting import effective_emvr_stage_payload, stage_report_section
from tests.test_builder_requirements import VALID_VALUES
from tests.test_engine import continue_emvr


def make_session(stage=Stage.EXPECTED_DATA_VISUALIZATION):
    session = DesignSession(
        design_id="emvr33_regression", interaction_state=InteractionState.EMVR_DIRECT,
        current_stage_index=list(Stage).index(stage),
        design_context={
            "stage_design_state": deepcopy(VALID_VALUES),
            "emvr_design": {
                "formula_flow": {"formula_selection": {"primary_formula_ids": ["coulomb_point_charge"]}},
                "field_state": {
                    "changed_quantities": ["距离", "电荷类型"],
                    "observed_quantities": ["场线空间形态"],
                    "visualization_requirements": ["在同一距离下并排比较同号和异号场线，固定视角和种子。"],
                },
            },
        },
    )
    return session


def set_pending(session, field):
    pending = {
        "action_id": f"ask-{field}", "type": "ANSWER_EMVR_STAGE_QUESTION",
        "interaction_state": "EMVR_DIRECT", "stage": session.current_stage.value,
        "subject": field, "answer_fields": [field], "question": f"请明确{field}",
        "allowed_intents": ["ANSWER_CURRENT_QUESTION", "REQUEST_MORE_EXAMPLES", "UNCLEAR"],
    }
    if field == "implementation_defaults":
        pending.update(default_proposal_value=format_implementation_defaults(build_implementation_defaults(session)),
                       default_option_id="approve-builder-implementation-defaults")
    session.model_context["dialogue_state"] = {"pending_action": pending}
    set_pending_action_snapshot(session, pending)
    return pending


@pytest.mark.parametrize("field,stage,marker", [
    ("model_constants_and_media", Stage.VARIABLES_AND_CONDITIONS, "排除半径"),
    ("numerical_model_specifications", Stage.EXPECTED_DATA_VISUALIZATION, "RK4"),
    ("acceptance_criteria", Stage.RESULT_INTERPRETATION, "通过条件"),
    ("report_questions", Stage.RESULT_INTERPRETATION, "核心公式"),
])
def test_reference_and_acceptance_are_bound_to_the_pending_field(field, stage, marker):
    session = make_session(stage)
    session.design_context["stage_design_state"].pop(field)
    set_pending(session, field)
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    before = stage_design_state_snapshot(session)
    response = engine.process_turn(session.design_id, {"message": "给我一个参考"})
    draft = response["stage_payload"]["reference_draft"]
    assert draft["field"] == field
    assert marker in draft["value"]
    assert "横轴或主控制量" not in response["assistant_message"]
    stored = engine.store.get(session.design_id)
    assert stage_design_state_snapshot(stored) == before
    assert current_pending_action(stored)["candidate_answer"] == draft["value"]
    engine.process_turn(session.design_id, {"message": "采用"})
    stored = engine.store.get(session.design_id)
    assert builder_requirement_values(stored)[field] == draft["value"]
    assert stored.design_context["emvr_design"]["field_state"]["visualization_requirements"] == [
        "在同一距离下并排比较同号和异号场线，固定视角和种子。"
    ]


def test_reference_uses_answer_field_when_pending_subject_is_a_stage():
    session = make_session()
    pending = set_pending(session, "numerical_model_specifications")
    pending["subject"] = session.current_stage.value
    set_pending_action_snapshot(session, pending)
    session.model_context["dialogue_state"]["pending_action"] = pending
    output = _emvr_reference_output(session)
    assert output.stage_payload["reference_draft"]["field"] == "numerical_model_specifications"


def test_unrelated_formula_never_receives_charge_constants_or_rk4_defaults():
    session = make_session()
    # Biot-Savart now has its own magnetic field-line reference (emvr35).
    # A time-varying induction formula still must not inherit either template.
    session.design_context["emvr_design"]["formula_flow"]["formula_selection"]["primary_formula_ids"] = ["faraday_differential"]
    session.design_context["stage_design_state"].pop("numerical_model_specifications")
    set_pending(session, "numerical_model_specifications")
    output = _emvr_reference_output(session)
    assert "reference_draft" not in output.stage_payload
    assert "RK4" not in output.assistant_message
    assert "ε₀" not in output.assistant_message


def test_display_output_never_copies_control_turns_or_other_contract_answers():
    session = make_session()
    audit = ["采用", "场景首次打开：探针位于原点；Reset清除曲线。", "算法：RK4，最大500步"]
    session.design_context["emvr_design"]["stage_inputs"] = {
        session.current_stage.value: [{"content": text} for text in audit]
    }
    generated = RuleBasedStageGenerator().generate(session, "继续")
    session.stage_outputs[session.current_stage.value] = generated.to_dict()
    # Simulate the stale payload of an already completed older session.
    session.stage_outputs[session.current_stage.value]["stage_payload"]["student_visualization_requirements"] = audit
    payload = effective_emvr_stage_payload(session, session.current_stage)
    assert payload["student_visualization_requirements"] == ["在同一距离下并排比较同号和异号场线，固定视角和种子。"]
    assert "RK4" in payload["numerical_model_specifications"]
    assert session.design_context["emvr_design"]["stage_inputs"][session.current_stage.value][0]["content"] == "采用"


def test_legacy_display_prose_is_preserved_while_misfiled_turns_are_removed():
    session = make_session()
    session.design_context["emvr_design"]["field_state"].pop("visualization_requirements")
    display = "同一距离下比较两种配置，保持相同的色标与观察位置。"
    session.stage_outputs[session.current_stage.value] = {"stage_payload": {"student_visualization_requirements": [
        display, "采用", VALID_VALUES["measurement_specifications"],
        "场景首次打开：探针位于原点。Reset后恢复。", "算法：RK4；采样域：10 m立方体。",
    ]}}
    payload = effective_emvr_stage_payload(session, session.current_stage)
    assert payload["student_visualization_requirements"] == [display]


def test_late_no_probe_and_qualitative_contract_refreshes_all_generated_copies():
    session = make_session()
    state = session.design_context["stage_design_state"]
    old_reference = "每轮比较前恢复已确认的初始状态：探针位于原点；曲线和场图按默认参数显示。Reset清除数据。"
    state.update(
        initial_reset_state="Initial两电荷间距2 m，同号；Reset恢复初始配置并清除快照。",
        measurement_specifications="本实验不设置可移动空间探针；定性描述，不涉及数值计算；单位无，按固定视角采样。",
        reference_condition=old_reference,
        controlled_conditions="电荷量、介质、场线种子、相机或探针设置",
    )
    session.stage_outputs[Stage.THEORETICAL_FRAMEWORK.value] = {"stage_payload": {"reference_condition": old_reference}}
    session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {"stage_payload": {"procedure_steps": [
        "加载并记录基准状态：" + old_reference, "改变距离，保存快照", "并排比较场线"
    ]}}
    for stage in (Stage.THEORETICAL_FRAMEWORK, Stage.VARIABLES_AND_CONDITIONS, Stage.CONCEPTUAL_PROCEDURE):
        payload = effective_emvr_stage_payload(session, stage)
        text = json.dumps(payload, ensure_ascii=False)
        assert "探针位于原点" not in text
        assert "相机或探针设置" not in text
        assert "曲线和场图" not in text
    contract = build_implementation_defaults(session)
    assert "探针位于原点" not in contract["experiment_flow"]
    assert "保留已保存快照" in contract["experiment_flow"]
    assert "定性" in contract["measurement_and_chart_policy"]
    assert state["reference_condition"] == old_reference  # audit/canonical input remains inspectable


def test_reference_projection_without_an_initial_contract_does_not_recurse():
    session = make_session()
    state = session.design_context["stage_design_state"]
    state.pop("initial_reset_state")
    state["reference_condition"] = "每轮比较前恢复同一快照：参数起点"
    assert project_derived_contract_text(session, state["reference_condition"]) == state["reference_condition"]


def test_reexport_after_a_second_revision_refreshes_previously_projected_baselines():
    session = make_session()
    first = project_derived_contract_text(session, "每轮比较前恢复已确认的初始状态：旧值")
    session.design_context["stage_design_state"]["reference_condition"] = first
    session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {"stage_payload": {"procedure_steps": ["加载基准：" + first]}}
    session.design_context["stage_design_state"]["initial_reset_state"] = "Initial距离2.5 m，无探针；Reset恢复初始配置。"
    projected = project_derived_contract_text(session, first)
    assert "2.5 m" in projected and "1.0 m" not in projected
    assert project_derived_contract_text(session, projected) == projected
    steps = effective_emvr_stage_payload(session, Stage.CONCEPTUAL_PROCEDURE)["procedure_steps"]
    assert "2.5 m" in str(steps) and "1.0 m" not in str(steps)


def test_student_report_and_builder_choose_the_same_latest_contract():
    session = make_session(Stage.RESULT_INTERPRETATION)
    session.design_context["emvr_design"]["field_state"]["report_questions"] = ["新问题：请解释当前的比较结果。"]
    payload = effective_emvr_stage_payload(session, session.current_stage)
    assert payload["report_questions"] == builder_requirement_values(session)["report_questions"]
    assert "新问题" in payload["report_questions"]


def test_merging_and_restoring_across_write_paths_uses_latest_value():
    session = make_session(Stage.RESULT_INTERPRETATION)
    def write(value, operation="REPLACE"):
        apply_stage_field_updates(session, [{"field": "expected_results", "operation": operation, "value": value}], stage=session.current_stage)
    write("结果A")
    apply_emvr_field_updates(session.design_context["emvr_design"], {"field_updates": [
        {"field_id": "expected_results", "operation": "REPLACE", "value": ["结果B"]}
    ]})
    write("补充C", "MERGE")
    assert "结果B" in builder_requirement_values(session)["expected_results"]
    assert "结果A" not in builder_requirement_values(session)["expected_results"]
    write("结果A")
    assert builder_requirement_values(session)["expected_results"] == "结果A"


def test_late_distance_bands_in_acceptance_reopen_only_parameter_definition():
    session = make_session(Stage.RESULT_INTERPRETATION)
    session.design_context["stage_design_state"]["acceptance_criteria"] = "完成远、中、近三种距离的观察"
    rows = missing_builder_requirements(session)
    row = next(item for item in rows if item["field"] == "parameter_specifications")
    assert "准确数值" in row["question"]
    assert "只需补充" in row["question"]


def test_long_display_contract_keeps_tail_in_both_stores_and_report():
    session = make_session()
    value = "固定视角比较完整空间分布。" * 500 + "末尾约束：禁止自动更改色标。"
    update = normalize_emvr_design_update({"field_updates": [
        {"field_id": "visualization_requirements", "operation": "REPLACE", "value": [value]},
    ]})
    apply_emvr_field_updates(session.design_context["emvr_design"], update)
    apply_stage_field_updates(session, [{"field": "visualization_plan", "operation": "REPLACE", "value": value}], stage=session.current_stage)
    payload = effective_emvr_stage_payload(session, session.current_stage)
    assert payload["student_visualization_requirements"] == value
    section = stage_report_section(session.current_stage, payload)
    assert "末尾约束" in json.dumps(section, ensure_ascii=False)


def test_snapshot_normalization_keeps_complete_scalars_and_lists():
    text = "完整约束。" * 400 + "末尾说明。"
    steps = [f"步骤{i}：{text}" for i in range(25)]
    update = normalize_emvr_design_update({"hypothesis": text, "visualization_requirements": steps})
    state = {}
    apply_emvr_field_updates(state, update)
    assert state["field_state"]["hypothesis"] == text
    assert state["field_state"]["visualization_requirements"] == steps


def test_completed_builder_export_preserves_display_requirements_separately_from_measurements():
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    result = engine.create_design("请用EMVR设计双点电荷电场叠加实验", interaction_state=InteractionState.EMVR_DIRECT)
    for _ in range(70):
        if result["workflow_status"] == "complete":
            break
        result = continue_emvr(engine, result)
    assert result["workflow_status"] == "complete"
    session = engine.store.get(result["design_id"])
    display = "同一距离的快照并排展示，并在图旁保留学生的差异解释。"
    apply_stage_field_updates(session, [{"field": "visualization_plan", "operation": "REPLACE", "value": display}], stage=Stage.EXPECTED_DATA_VISUALIZATION)
    contract = format_implementation_defaults(build_implementation_defaults(session))
    apply_stage_field_updates(session, [{"field": "implementation_defaults", "operation": "REPLACE", "value": contract}], stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    record_implementation_defaults_approval(session, source="TEST")
    payload = build_builder_gate1_input(session)
    rows = {row["key"]: row["value"] for row in payload["visualization"]}
    assert rows["visualization.requirements"] == display
    assert rows["visualization.metric_and_probe_definitions"] != display


def test_summarize_one_default_item_is_read_only_and_does_not_reprint_all():
    session = make_session(Stage.DESIGN_VALUE_AND_LIMITATIONS)
    set_pending(session, "implementation_defaults")
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    before = deepcopy(session.design_context["stage_design_state"])
    result = engine.process_turn(session.design_id, {"message": "第三条太臃肿了，尝试总结提炼一下"})
    assert result["stage_payload"]["presentation_only"]
    assert "Game View" in result["assistant_message"]
    assert "从零构建" not in result["assistant_message"]
    assert len(result["assistant_message"]) < 450
    stored = engine.store.get(session.design_id)
    assert stored.design_context["stage_design_state"] == before
    assert not implementation_defaults_approval_valid(stored)
    assert current_pending_action(stored)["subject"] == "implementation_defaults"
