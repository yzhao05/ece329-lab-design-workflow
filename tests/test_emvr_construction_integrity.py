from __future__ import annotations

from copy import deepcopy

from ece329_workflow.builder_defaults import (
    build_implementation_defaults,
    format_implementation_defaults,
    implementation_defaults_approval_valid,
    record_implementation_defaults_approval,
)
from ece329_workflow.builder_input import _student_task_contracts
from ece329_workflow.builder_requirements import missing_builder_requirements
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import current_pending_action, set_pending_action_snapshot
from ece329_workflow.emvr_design import apply_emvr_field_updates, normalize_emvr_design_update
from ece329_workflow.engine import WorkflowEngine, _implementation_defaults_approval_intent
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import DesignSession, InteractionState, Stage, TurnRequest, WorkflowStatus
from ece329_workflow.reporting import effective_emvr_stage_payload
from tests.test_builder_requirements import VALID_VALUES


def session_with_contract():
    return DesignSession(
        design_id="construction_integrity",
        interaction_state=InteractionState.EMVR_DIRECT,
        current_stage_index=list(Stage).index(Stage.DESIGN_VALUE_AND_LIMITATIONS),
        design_context={"stage_design_state": deepcopy(VALID_VALUES)},
    )


def pending_defaults(session):
    return {
        "action_id": "approve-construction",
        "type": "ANSWER_EMVR_STAGE_QUESTION",
        "stage": Stage.DESIGN_VALUE_AND_LIMITATIONS.value,
        "interaction_state": InteractionState.EMVR_DIRECT.value,
        "subject": "implementation_defaults",
        "answer_fields": ["implementation_defaults"],
        "question": "是否批准以下实现默认方案？",
        "default_proposal_value": format_implementation_defaults(build_implementation_defaults(session)),
        "default_option_id": "approve-builder-implementation-defaults",
        "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
    }


def test_replacing_a_with_b_then_a_is_not_a_historical_duplicate():
    session = session_with_contract()
    for value in ("预期结果A", "预期结果B", "预期结果A"):
        assert apply_stage_field_updates(session, [{
            "field": "expected_results", "operation": "REPLACE", "value": value,
        }], stage=Stage.RESULT_INTERPRETATION) == ["expected_results"]
    assert session.design_context["stage_design_state"]["expected_results"] == "预期结果A"
    assert apply_stage_field_updates(session, [{
        "field": "expected_results", "operation": "REPLACE", "value": "预期结果A",
    }], stage=Stage.RESULT_INTERPRETATION) == []


def test_full_default_document_survives_pending_and_stage_storage():
    session = session_with_contract()
    session.design_context["stage_design_state"]["numerical_model_specifications"] += "边界说明。" * 2600
    pending = pending_defaults(session)
    proposal = pending["default_proposal_value"]
    assert len(proposal) > 12000
    session.model_context["dialogue_state"] = {"pending_action": pending}
    set_pending_action_snapshot(session, pending)
    assert current_pending_action(session)["default_proposal_value"] == proposal
    apply_stage_field_updates(session, [{
        "field": "implementation_defaults", "operation": "REPLACE", "value": proposal,
    }], stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    assert session.design_context["stage_design_state"]["implementation_defaults"] == proposal


def test_detailed_parameter_and_procedure_updates_keep_their_tail():
    text = "采样步长0.01 m，" * 120 + "最终边界值0.5 m"
    steps = [f"步骤{i}：{text}" for i in range(25)]
    update = normalize_emvr_design_update({"field_updates": [
        {"field_id": "parameter_specifications", "operation": "REPLACE", "value": text},
        {"field_id": "procedure_steps", "operation": "REPLACE", "value": steps},
    ]})
    state = {}
    apply_emvr_field_updates(state, update)
    assert state["field_state"]["parameter_specifications"] == [text]
    assert state["field_state"]["procedure_steps"] == steps


def test_nested_formula_selection_and_contract_edits_invalidate_approval():
    session = session_with_contract()
    session.design_context["emvr_design"] = {"formula_flow": {"formula_selection": {"primary": ["old"]}}}
    record_implementation_defaults_approval(session, source="TEST")
    assert implementation_defaults_approval_valid(session)
    session.design_context["emvr_design"]["formula_flow"]["formula_selection"]["primary"] = ["new"]
    assert not implementation_defaults_approval_valid(session)
    record_implementation_defaults_approval(session, source="TEST")
    session.design_context["stage_design_state"]["implementation_defaults"] += "修改界面"
    assert not implementation_defaults_approval_valid(session)


def test_question_with_approval_button_does_not_take_approval_shortcut():
    session = session_with_contract()
    pending = pending_defaults(session)
    request = TurnRequest(message="为什么要使用这个误差容差？", selected_option_id=pending["default_option_id"])
    assert _implementation_defaults_approval_intent(session, request, request.message, pending) is None


def test_stale_default_button_refreshes_once_instead_of_approving_old_content():
    session = session_with_contract()
    pending = pending_defaults(session)
    session.design_context["stage_design_state"]["lab_title"] = "更新后的实验标题"
    session.model_context["dialogue_state"] = {"pending_action": pending}
    set_pending_action_snapshot(session, pending)
    session.stage_outputs[Stage.DESIGN_VALUE_AND_LIMITATIONS.value] = {"stage_payload": {}}
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {"message": "批准默认方案", "selected_option_id": pending["default_option_id"]})
    stored = engine.store.get(session.design_id)
    assert result["workflow_status"] != "complete"
    refreshed = current_pending_action(stored)
    assert "更新后的实验标题" in refreshed["default_proposal_value"]
    assert refreshed["default_proposal_value"] != pending["default_proposal_value"]
    assert not implementation_defaults_approval_valid(stored)
    intent = _implementation_defaults_approval_intent(stored, TurnRequest(message="批准默认方案"), "批准默认方案", refreshed)
    assert intent["source"] != "STALE_IMPLEMENTATION_DEFAULTS"


def test_audit_history_and_old_defaults_cannot_create_distance_or_radius_blockers():
    session = session_with_contract()
    session.design_context["stage_design_state"]["implementation_defaults"] += "旧方案近、中、远距离与排除半径"
    session.design_context["emvr_design"] = {"field_history": [{"value": "近、中、远距离；排除半径"}]}
    record_implementation_defaults_approval(session, source="TEST")
    missing = {row["field"] for row in missing_builder_requirements(session)}
    assert "parameter_specifications" not in missing
    assert "model_constants_and_media" not in missing


def test_missing_numerical_algorithm_blocks_export_without_reopening_other_fields():
    session = session_with_contract()
    session.design_context["stage_design_state"]["numerical_model_specifications"] = ""
    record_implementation_defaults_approval(session, source="TEST")
    assert {row["field"] for row in missing_builder_requirements(session)} == {"numerical_model_specifications"}


def test_default_blueprint_contains_construction_bindings_and_finite_execution():
    text = pending_defaults(session_with_contract())["default_proposal_value"]
    for marker in ("ProjectVersion.txt", "EmVrLabContractRunner", "EmVrObjectStateSnapshotStore", "取消", "OnDisable", "RK4", "2048", "Capture",
                   "InitializeOnce", "INITIALIZING/READY/FAILED", "10 s", "Awake", "脚本域重载"):
        assert marker in text


def test_upgraded_snapshot_contract_requires_review_then_stays_approved():
    session = session_with_contract()
    record_implementation_defaults_approval(session, source='TEST')
    session.design_context['emvr_design']['implementation_defaults_approval']['contract_version'] = 'builder-ui-flow-v8'
    assert not implementation_defaults_approval_valid(session)
    proposal = pending_defaults(session)['default_proposal_value']
    for marker in ('--mode integrated-development', 'schema_version', 'run_id',
                   '允许已确认的自变量', '不部分覆盖', '回滚完整领域及对象状态'):
        assert marker in proposal
    apply_stage_field_updates(session, [{'field': 'implementation_defaults', 'operation': 'REPLACE', 'value': proposal}],
                              stage=Stage.DESIGN_VALUE_AND_LIMITATIONS)
    record_implementation_defaults_approval(session, source='TEST')
    assert implementation_defaults_approval_valid(session)
    assert 'implementation_defaults' not in {row['field'] for row in missing_builder_requirements(session)}
    session.history.append({'user_message': '你现在在问什么？', 'output': {}})
    assert implementation_defaults_approval_valid(session)


def test_compound_procedure_actions_have_reachable_capture_transitions():
    tasks = _student_task_contracts(["建立基准并保存快照", "改变距离后记录结果", "比较保存的两组快照"], "场强V/m")
    assert tasks[0]["exit_state"] == "CAPTURED"
    assert tasks[1]["exit_state"] == "CAPTURED"
    assert tasks[2]["exit_state"] == "COMPARING"
    assert all("COMPLETE" not in item["exit_state"] for item in tasks)


def test_comparison_context_and_interpretation_are_not_misread_as_actions():
    tasks = _student_task_contracts([
        "每轮比较前加载并记录基准状态",
        "对每种比较情形保存快照",
        "依据公式解释差异，并记录超出模型范围的情况",
    ], "场强V/m")
    assert [task["exit_state"] for task in tasks] == ["CAPTURED", "CAPTURED", "COMPLETE"]


def test_shortened_procedure_is_not_replaced_by_longer_old_payload():
    session = session_with_contract()
    session.design_context["emvr_design"] = {"field_state": {"procedure_steps": ["新的步骤一", "新的步骤二"]}}
    session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {"stage_payload": {"procedure_steps": ["旧步骤"] * 7}}
    payload = effective_emvr_stage_payload(session, Stage.CONCEPTUAL_PROCEDURE)
    assert payload["procedure_steps"] == ["新的步骤一", "新的步骤二"]


def test_numerical_contract_is_projected_from_latest_value():
    session = session_with_contract()
    payload = effective_emvr_stage_payload(session, Stage.EXPECTED_DATA_VISUALIZATION)
    assert payload["numerical_model_specifications"] == VALID_VALUES["numerical_model_specifications"]


def test_non_charge_experiment_does_not_get_charge_specific_troubleshooting():
    session = DesignSession(design_id="generic", interaction_state=InteractionState.EMVR_DIRECT)
    payload = effective_emvr_stage_payload(session, Stage.RESULT_INTERPRETATION)
    assert "电荷" not in payload["if_opposite_trend"]
    assert "E_total" not in payload["if_no_clear_change"]


def test_confirmed_feasibility_is_not_overwritten_by_point_charge_defaults():
    session = session_with_contract()
    session.design_context["emvr_design"] = {"selected_primary_formula_ids": ["coulomb_point_charge"]}
    for field in ("conceptual_feasibility", "teaching_value", "vr_added_value"):
        session.design_context["stage_design_state"][field] = "已确认的谨慎评价：需先验证精度"
    payload = effective_emvr_stage_payload(session, Stage.DESIGN_VALUE_AND_LIMITATIONS)
    for field in ("conceptual_feasibility", "teaching_value", "vr_added_value"):
        assert payload[field] == "已确认的谨慎评价：需先验证精度"


def test_old_completed_session_reopens_missing_handoff_without_erasing_design():
    session = session_with_contract()
    session.status = WorkflowStatus.COMPLETE
    session.design_context["stage_design_state"].pop("numerical_model_specifications")
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {"message": "继续检查并补充交接内容"})
    stored = engine.store.get(session.design_id)
    assert stored.status is WorkflowStatus.ACTIVE
    assert stored.design_context["stage_design_state"]["parameter_specifications"] == VALID_VALUES["parameter_specifications"]
    assert "重新开放最终检查" in result["assistant_message"]
    assert result["stage_payload"]["builder_requirement_field"] == "numerical_model_specifications"
