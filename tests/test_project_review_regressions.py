"""Regression coverage for retry liveness, source closure, and construction inputs."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from ece329_workflow.builder_defaults import (
    build_implementation_defaults, format_implementation_defaults,
    implementation_defaults_approval_valid, record_implementation_defaults_approval,
)
from ece329_workflow.builder_portability import embed_supplied_references, validate_portable_content
from ece329_workflow.builder_input import _student_task_contracts, build_builder_gate1_input, validate_builder_gate1_input
from ece329_workflow.builder_requirements import missing_builder_requirements
from ece329_workflow.dialogue_state import current_pending_action, record_pending_clarification
from ece329_workflow.design_state import set_pending_action_snapshot
from ece329_workflow.engine import WorkflowEngine, _normalize_final_source_references
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.models import Stage, StepOutput, WorkflowStatus, InteractionState
from tests.test_engine import continue_emvr
from tests.test_emvr_construction_integrity import session_with_contract, pending_defaults


@pytest.fixture(scope="module")
def completed_design():
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    result = engine.create_design("请用EMVR设计双点电荷电场叠加实验", interaction_state=InteractionState.EMVR_DIRECT)
    for _ in range(70):
        if result["workflow_status"] == "complete":
            return engine.store.get(result["design_id"])
        result = continue_emvr(engine, result)
    pytest.fail("Synthetic workflow failed to complete within 70 user decisions")


def test_repeated_final_generation_has_bounded_nonrecursive_sources(completed_design):
    session = deepcopy(completed_design)
    generator = RuleBasedStageGenerator()
    sizes = []
    for _ in range(12):
        output = generator.generate(session, "继续")
        session.stage_outputs[session.current_stage.value] = output.to_dict()
        final = output.stage_payload["final_design"]
        assert "stage_outputs" not in final
        assert session.current_stage.value not in final["source_stage_ids"]
        sizes.append(len(json.dumps(output.to_dict(), ensure_ascii=False)))
    assert max(sizes) - min(sizes) < 100


def test_online_final_payload_cannot_persist_previous_report_snapshots(completed_design):
    session = deepcopy(completed_design)
    output = StepOutput(assistant_message="ready", student_task="review", stage_payload={
        "final_design": {"summary": "current", "stage_outputs": deepcopy(session.stage_outputs), "history": ["old"]},
        "source_stage_outputs": deepcopy(session.stage_outputs),
    })
    _normalize_final_source_references(session, Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT, output)
    assert output.stage_payload["final_design"]["summary"] == "current"
    assert "stage_outputs" not in output.stage_payload["final_design"]
    assert "history" not in output.stage_payload["final_design"]
    assert isinstance(output.stage_payload["source_stage_outputs"], list)


def test_artifact_only_failure_reopens_and_identical_retries_do_not_grow_history(completed_design):
    session = deepcopy(completed_design)
    generator = RuleBasedStageGenerator()
    engine = WorkflowEngine(generator=generator)
    engine.store.save(session)
    with patch("ece329_workflow.engine.build_builder_gate1_input", side_effect=ValueError("Broken embedded source closure")):
        first = engine.process_turn(session.design_id, {"message": "继续", "complete_stage": True})
        assert first["workflow_status"] == "active"
        assert "Broken embedded source closure" in first["completion_error"]
        assert first["stage_payload"]["artifact_blocker"]["retry_requires_change"] is True
        stored = engine.store.get(session.design_id)
        revision, history_length = stored.revision, len(stored.history)
        with patch.object(generator, "generate", side_effect=AssertionError("Identical retry regenerated report")):
            for index in range(6):
                response = engine.process_turn(session.design_id, {"message": "继续", "turn_id": f"retry-turn-{index}",
                    "selected_option_id": "stale-continue-button" if index % 2 else None,
                    "complete_stage": True})
                assert response["revision"] == revision
                assert response["builder_input_ready"] is False
        assert len(engine.store.get(session.design_id).history) == history_length
    # A real input change may retry after the exporter has been repaired.
    response = engine.process_turn(session.design_id, {"message": "重新检查交付", "complete_stage": True,
                                                          "context_patch": {"builder_reference_material": []}})
    assert response["workflow_status"] == "complete"
    assert response["builder_input_ready"] is True


def test_bad_reference_patch_rejected_without_mutating_design():
    session = session_with_contract()
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    before = deepcopy(engine.store.get(session.design_id).design_context)
    with pytest.raises(ValueError, match="copied content"):
        engine.process_turn(session.design_id, {"message": "补充依据", "context_patch": {
            "builder_reference_material": [{"source_path": "outside/lecture.pdf"}],
        }})
    assert engine.store.get(session.design_id).design_context == before


def test_external_reference_repair_targets_its_owner_and_source_edit_invalidates_approval():
    session = session_with_contract()
    field = "parameter_specifications"
    source = "lecture_notes/constraints.pdf"
    session.design_context["stage_design_state"][field] += f"；约束参考 {source}"
    record_implementation_defaults_approval(session, source="TEST")
    assert {item["field"] for item in missing_builder_requirements(session)} == {field}
    session.design_context["builder_reference_material"] = [{"source_path": source, "content": "距离必须在0.5至5.0米内。"}]
    assert not implementation_defaults_approval_valid(session)
    record_implementation_defaults_approval(session, source="TEST")
    assert not missing_builder_requirements(session)
    session.design_context["builder_reference_material"][0]["content"] += "限值也适用于XR拖动。"
    assert not implementation_defaults_approval_valid(session)


def test_long_candidate_survives_clarification_and_pending_persistence():
    session = session_with_contract()
    pending = pending_defaults(session)
    pending["answer_fields"] = ["numerical_model_specifications"]
    pending["subject"] = "numerical_model_specifications"
    session.model_context["dialogue_state"] = {"pending_action": pending}
    text = "统一积分边界说明。" * 400 + "末尾约束：容差0.001米且取消后不得更新旧结果。"
    record_pending_clarification(session, text, allow_exact_field_binding=True)
    set_pending_action_snapshot(session, session.model_context["dialogue_state"]["pending_action"])
    engine = WorkflowEngine(generator=RuleBasedStageGenerator())
    engine.store.save(session)
    assert current_pending_action(engine.store.get(session.design_id))["candidate_answer"] == text


@pytest.mark.parametrize("value", [
    {"D:/outside/source.pdf": "copied?"}, "notes/%252e%252e/source.pdf",
    "课程/讲义.pdf", "private/results.csv", "private/screenshot.png",
])
def test_boundary_checks_encoded_paths_unicode_and_dictionary_keys(value):
    with pytest.raises(ValueError):
        validate_portable_content(value)


def test_duplicate_partial_and_missing_internal_references_cannot_mask_a_broken_link():
    source = "outside/a.pdf"
    reference = {"source_path": source, "content": "E = E_A + E_B"}
    with pytest.raises(ValueError, match="duplicate"):
        embed_supplied_references({}, [reference, reference])
    replaced, _ = embed_supplied_references({"source": source + ".bak"}, [reference])
    assert replaced["source"] == source + ".bak"
    with pytest.raises(ValueError, match="no copied-content"):
        embed_supplied_references({"source": "本 PDF 内嵌参考 REF_03"}, [reference])
    with pytest.raises(ValueError, match="arbitrary word"):
        embed_supplied_references("E = E_A + E_B", [{"source_path": "E", "content": "field"}])


def test_construction_blueprint_preserves_lifecycle_and_defines_bootstrap_evidence():
    session = session_with_contract()
    session.design_context["stage_design_state"]["initial_reset_state"] = "Reset保留已保存快照，清除未保存预览。"
    session.design_context["stage_design_state"]["hidden_object_lifecycle"] = "初始隐藏比较面板；保存两组兼容快照后显示。"
    text = format_implementation_defaults(build_implementation_defaults(session))
    validate_portable_content(text)
    for marker in ("Reset保留已保存快照", "初始隐藏比较面板", "source-references.json", "Build Scene",
                   "telemetry_map", "OnDisable", "证据", "asmdef", "labflow source-references", "不能手写或更改",
                   "种子数乘每条轨迹步数上限乘每步最大场求值次数"):
        assert marker in text
    assert "No hidden startup objects" not in text


def test_compare_exit_is_conditional_on_real_evidence_not_a_single_capture():
    task = _student_task_contracts(["改变距离并保存快照后并排比较"], "定性空间比较")[0]
    assert "at least two valid compatible snapshot IDs" in task["observable_evidence"]
    assert "otherwise retain the current valid state" in task["unity_response"]
    assert "never a failed action" in task["success_criteria"]


@pytest.mark.parametrize("defect", ["duplicate_step", "unknown_object", "missing_reference"])
def test_final_artifact_rejects_disconnected_identifiers(completed_design, defect):
    payload = build_builder_gate1_input(deepcopy(completed_design))
    if defect == "duplicate_step":
        payload["student_tasks"][1]["step_id"] = "S1"
        error = "step IDs"
    elif defect == "unknown_object":
        payload["student_tasks"][1]["goal"] += "；操作 OBJ_999"
        error = "unknown object"
    else:
        payload["student_tasks"][1]["goal"] += "；按本 PDF 内嵌参考 REF_99"
        error = "no copied-content"
    with pytest.raises(ValueError, match=error):
        validate_builder_gate1_input(payload)
