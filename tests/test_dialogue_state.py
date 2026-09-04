from __future__ import annotations

import unittest

from ece329_workflow.dialogue_state import (
    UserIntent,
    apply_resolved_intent,
    build_carried_context,
    clarification_output,
    current_pending_action,
    degraded_context_intent,
    deterministic_intent,
    fallback_intent,
    hydrate_pending_action_from_history,
    recover_repeated_pending_answer,
    record_pending_clarification,
    STAGE_ONE_DIRECTION_CANDIDATE,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from ece329_workflow.engine import (
    WorkflowEngine,
    _guided_stage_should_auto_advance,
    _remove_repeated_guided_question,
)
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.generator import guided_stage_entry_output
from ece329_workflow.emvr_design import (
    EMVR_EDITABLE_FIELDS,
    apply_emvr_field_updates,
    merge_emvr_structured_requirements,
    normalize_emvr_design_update,
)
from ece329_workflow.design_state import (
    apply_design_updates,
    design_state_snapshot,
    ensure_design_state,
    set_baseline_comparisons,
)
from ece329_workflow.idea_development import (
    build_facet_reference_output,
    build_gap_output,
    initialize_idea_development,
    update_idea_development,
)
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput
from ece329_workflow.dialogue_acts import (
    apply_stage_field_updates,
    stage_design_state_snapshot,
)


class ScriptedSemanticGenerator(RuleBasedStageGenerator):
    def __init__(
        self,
        intent: UserIntent,
        *,
        confidence: float = 0.97,
        semantic_updates: dict | None = None,
        target: str | None = None,
    ) -> None:
        self.intent = intent
        self.confidence = confidence
        self.semantic_updates = semantic_updates or {}
        self.target = target
        self.calls: list[dict] = []

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        self.calls.append(
            {
                "message": user_message,
                "pending_action": pending_action,
                "carried_context": carried_context,
            }
        )
        return resolved_intent(
            self.intent,
            target=(
                self.target
                if self.target is not None
                else str(pending_action.get("subject") or "")
                if pending_action
                else None
            ),
            resolved_value=(pending_action.get("proposal") if pending_action else None),
            preserve_current_design=True,
            confidence=self.confidence,
            source="SEMANTIC_TEST",
            semantic_updates=self.semantic_updates,
        )


class MultiActSemanticGenerator(RuleBasedStageGenerator):
    def __init__(
        self,
        acts: list[dict],
        *,
        semantic_updates: dict | None = None,
    ) -> None:
        self.acts = acts
        self.semantic_updates = semantic_updates or {}
        self.calls: list[dict] = []

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        self.calls.append(
            {
                "message": user_message,
                "pending_action": pending_action,
                "carried_context": carried_context,
            }
        )
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.98,
            source="SEMANTIC_MODEL",
            semantic_updates=self.semantic_updates,
            dialogue_acts=self.acts,
            actions_authoritative=True,
        )


class SequencedSemanticGenerator(RuleBasedStageGenerator):
    """Return one contextual semantic result per turn for workflow regressions."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.calls: list[dict] = []

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        self.calls.append(
            {
                "message": user_message,
                "pending_action": pending_action,
                "carried_context": carried_context,
            }
        )
        step = self.steps.pop(0)
        return (
            step(session, user_message, pending_action, carried_context)
            if callable(step)
            else step
        )


def variable_stage_session(design_id: str) -> DesignSession:
    session = DesignSession(
        design_id=design_id,
        interaction_state=InteractionState.GUIDED_DESIGN,
        current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
        design_context={
            "idea": {"main_direction": "比较两个场源靠近时的电场线变化"}
        },
    )
    session.stage_outputs[Stage.VARIABLES_AND_CONDITIONS.value] = {
        "stage_payload": {
            "independent_variable": "两个源之间的距离",
            "dependent_variable": ["电场线形状", "中间区域通量"],
            "controlled_variables": ["源强", "观察方式"],
        }
    }
    save_pending_action(
        session,
        Stage.VARIABLES_AND_CONDITIONS,
        StepOutput(
            assistant_message="当前变量草案可继续修改。",
            stage_payload={
                "proposal": {
                    "independent_variable": "两个源之间的距离",
                    "observations": ["电场线形状", "中间区域通量"],
                }
            },
            student_task="请说明是保留、修改，还是完成这一部分后继续。",
        ),
    )
    return session


def idea_facet_session(design_id: str) -> DesignSession:
    session = DesignSession(
        design_id=design_id,
        interaction_state=InteractionState.GUIDED_DESIGN,
        design_context={
            "idea": {
                "original": "研究两个电荷源靠近时电场线的变化",
                "topic_anchor": "静电场中多个物体的相互影响",
                "current_focus": "比较两个电荷源靠近时电场线的变化",
                "course_scope_confirmed": True,
                "standard_comparisons": [
                    {
                        "comparison_id": "polarity_cases",
                        "recommended_cases": ["同种电荷", "异种电荷"],
                        "cases": ["同种电荷", "异种电荷"],
                        "adoption_status": "PENDING",
                    }
                ],
            }
        },
    )
    initialize_idea_development(
        session,
        {
            "core_phenomenon": "两个电荷源靠近时电场线发生变化",
            "course_relationships": ["静电场中的场源与空间分布"],
        },
    )
    output = build_gap_output(session, "")
    save_pending_action(session, Stage.IDEA_BRAINSTORMING, output)
    return session


class DialogueStateTests(unittest.TestCase):
    def test_guided_service_failure_candidate_can_rebind_to_exact_open_field(self) -> None:
        session = idea_facet_session("design_guided_retry_binding")
        pending = current_pending_action(session)
        assert pending is not None
        answer = "能够比较两种材料边界对静电场线弯曲与分布的影响"

        retained = record_pending_clarification(
            session,
            answer,
            allow_exact_field_binding=True,
        )

        assert retained is not None
        self.assertTrue(retained["candidate_binding_authorized"])
        accepted = validate_resolved_intent(
            resolved_intent(UserIntent.ACCEPT_PREVIOUS_PROPOSAL, confidence=0.98),
            retained,
        )
        self.assertEqual(accepted["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value)
        updates = accepted["semantic_updates"]["design_updates"]
        self.assertEqual(updates[0]["value"], answer)

    def test_action_contract_splits_pending_answer_and_comparison_replacement(self) -> None:
        session = idea_facet_session("design_mixed_learning_and_comparison")
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "boundary_cases",
                    "cases": ["完整包围场源", "不包围场源"],
                    "recommended_cases": ["完整包围场源", "不包围场源"],
                    "adoption_status": "ACCEPTED",
                }
            ],
        )
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "learning_objective",
            "question": "完成实验后希望能够解释什么？",
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        learning = "解释边界形状如何影响局部场分布，同时判断闭合面总通量是否改变"
        message = (
            f"学习目标是{learning}。另外把基础比较改成完整包围场源、部分包围场源和不包围场源。"
        )
        raw = resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target="learning_objective",
            resolved_value=message,
            confidence=0.99,
            source="SEMANTIC_MODEL",
            semantic_updates={
                "design_updates": [
                    {
                        "field": "learning_objective",
                        "operation": "REPLACE",
                        "value": message,
                    }
                ]
            },
            dialogue_acts=[
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": "learning_objective",
                    "operation": "REPLACE",
                    "content": learning,
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_COMPARISON",
                    "target": "boundary_cases",
                    "operation": "REPLACE",
                    "content": {
                        "comparison_id": "boundary_cases",
                        "action": "REPLACE",
                        "cases": ["完整包围场源", "部分包围场源", "不包围场源"],
                        "replace_all": True,
                    },
                    "confidence": 0.99,
                },
            ],
            actions_authoritative=True,
        )

        resolved = validate_resolved_intent(raw, pending)
        apply_resolved_intent(session, resolved, pending, message)
        state = design_state_snapshot(session)

        self.assertEqual(state["learning_objective"], learning)
        self.assertNotIn("另外", state["learning_objective"])
        self.assertEqual(
            state["baseline_comparisons"][0]["cases"],
            ["完整包围场源", "部分包围场源", "不包围场源"],
        )

    def test_authoritative_empty_action_array_cannot_copy_whole_turn_to_facet(self) -> None:
        session = idea_facet_session("design_no_whole_turn_fallback")
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "learning_objective",
            "question": "学习目标是什么？",
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        message = "学习目标是什么？另外我需要确认你是否替换基础比较。"
        raw = resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target="learning_objective",
            resolved_value=message,
            confidence=0.99,
            source="SEMANTIC_MODEL",
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "learning_objective", "status": "CLEAR"}
                ]
            },
            dialogue_acts=[],
            actions_authoritative=True,
            unresolved_content=[
                {"type": "UNRESOLVED", "content": message, "reason": "no acts"}
            ],
        )

        resolved = validate_resolved_intent(raw, pending)
        apply_resolved_intent(session, resolved, pending, message)

        self.assertEqual(resolved["intent"], UserIntent.UNCLEAR.value)
        self.assertEqual(design_state_snapshot(session)["learning_objective"], "")

    def test_cross_stage_field_edit_does_not_clear_current_pending_item(self) -> None:
        session = variable_stage_session("design_cross_stage_edit")
        pending = current_pending_action(session)
        raw = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            target="research_question",
            confidence=0.99,
            source="SEMANTIC_MODEL",
            dialogue_acts=[
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": "闭合面形状改变时，局部场强如何变化而总通量是否保持不变？",
                    "confidence": 0.99,
                }
            ],
            actions_authoritative=True,
        )

        resolved = validate_resolved_intent(raw, pending)
        apply_resolved_intent(session, resolved, pending, "在变量阶段改写研究问题")

        self.assertIn("总通量", design_state_snapshot(session)["research_question"])
        self.assertEqual(
            current_pending_action(session)["subject"],
            pending["subject"],
        )

    def test_accepting_saved_confirmation_consumes_it_and_advances(self) -> None:
        session = variable_stage_session("design_confirm_yes_advances")
        pending = session.model_context["dialogue_state"]["pending_action"]
        pending.update(
            {
                "type": "CONFIRM_STAGE_OR_MODIFY",
                "candidate_answer": "补充观察中间区域的场线弯曲程度",
                "candidate_resolution": UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                "advance_on_accept": True,
                "allowed_intents": [
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                    UserIntent.ADVANCE_STAGE.value,
                    UserIntent.UNCLEAR.value,
                ],
            }
        )
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "CONTROL",
                        "target": "ACCEPT",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    }
                ]
            )
        )
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": "是"})

        self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_PROCEDURE.value)
        self.assertNotIn(
            "请告诉我是否把这项补充接进当前设计",
            result["assistant_message"],
        )

    def test_structured_correction_repairs_only_named_field(self) -> None:
        session = idea_facet_session("design_structured_correction")
        raw = resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                confidence=0.99,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "CORRECT_ASSISTANT",
                        "target": "learning_objective",
                        "operation": "REPLACE",
                        "content": {
                            "error_type": "META_TEXT_CONTAMINATION",
                            "explanation": "删除误写入的会话说明",
                            "affected_fields": ["learning_objective"],
                            "design_updates": [
                                {
                                    "field": "learning_objective",
                                    "operation": "REPLACE",
                                    "value": "解释局部场分布与闭合面总通量之间的区别",
                                }
                            ],
                        },
                        "confidence": 0.99,
                    }
                ],
                actions_authoritative=True,
        )
        pending = current_pending_action(session)
        resolved = validate_resolved_intent(raw, pending)
        apply_resolved_intent(
            session,
            resolved,
            pending,
            "删除学习目标中的确认说明",
        )

        self.assertEqual(
            design_state_snapshot(session)["learning_objective"],
            "解释局部场分布与闭合面总通量之间的区别",
        )

    def test_theory_links_require_field_binding_and_support_targeted_removal(self) -> None:
        session = DesignSession(
            design_id="design_theory_relevance",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.THEORETICAL_FRAMEWORK),
        )
        pending = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "subject": Stage.THEORETICAL_FRAMEWORK.value,
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        raw = resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            confidence=0.99,
            source="SEMANTIC_MODEL",
            semantic_updates={
                "emvr_design_update": {
                    "theory_links": [
                        {
                            "relation_id": "ELECTROSTATIC_BOUNDARY",
                            "supports_design_content": "解释不同导体边界附近的场线方向",
                            "supports_design_fields": ["object_constraints", "observed_quantities"],
                        },
                        {
                            "relation_id": "OHMIC_CONDUCTION",
                            "supports_design_content": "属于ECE329课程",
                            "supports_design_fields": [],
                        },
                    ]
                }
            },
            dialogue_acts=[
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": Stage.THEORETICAL_FRAMEWORK.value,
                    "operation": "REPLACE",
                    "content": "只保留能解释边界形状与场分布的理论关系",
                    "confidence": 0.99,
                }
            ],
            actions_authoritative=True,
        )

        resolved = validate_resolved_intent(raw, pending)
        links = resolved["semantic_updates"]["emvr_design_update"]["theory_links"]

        self.assertEqual(
            [item["relation_id"] for item in links],
            ["ELECTROSTATIC_BOUNDARY"],
        )

        emvr_design: dict = {
            "field_state": {
                "object_constraints": ["比较不同导体边界"],
                "observed_quantities": ["边界附近的场线方向"],
            }
        }
        initial = normalize_emvr_design_update(
            {
                "theory_links": [
                    {
                        "relation_id": "ELECTROSTATIC_BOUNDARY",
                        "supports_design_content": "解释边界附近场线方向",
                        "supports_design_fields": ["object_constraints"],
                    },
                    {
                        "relation_id": "OHMIC_CONDUCTION",
                        "supports_design_content": "解释导电电流响应",
                        "supports_design_fields": ["observed_quantities"],
                    },
                ]
            }
        )
        apply_emvr_field_updates(emvr_design, initial)
        removal = normalize_emvr_design_update(
            {
                "theory_link_updates": [
                    {"relation_id": "OHMIC_CONDUCTION", "operation": "REMOVE"}
                ]
            }
        )
        apply_emvr_field_updates(emvr_design, removal)

        merged = merge_emvr_structured_requirements(emvr_design)
        self.assertEqual(
            [item["relation_id"] for item in merged["theory_links"]],
            ["ELECTROSTATIC_BOUNDARY"],
        )

    def test_parser_outage_keeps_open_question_and_does_not_write_raw_turn(self) -> None:
        session = idea_facet_session("design_parser_outage_local_clarification")
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "learning_objective",
            "question": "完成实验后希望能够解释什么？",
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        message = (
            "学习目标是解释边界形状和局部场分布；另外把基础比较换成三种边界。"
        )

        resolved = degraded_context_intent(
            session,
            message,
            pending,
            build_carried_context(session),
            source="INTENT_API_FAILURE",
        )
        apply_resolved_intent(session, resolved, pending, message)

        self.assertEqual(resolved["intent"], UserIntent.UNCLEAR.value)
        self.assertEqual(design_state_snapshot(session)["learning_objective"], "")
        self.assertEqual(
            resolved["unresolved_content"][0]["content"],
            message,
        )
        self.assertEqual(
            resolved["semantic_updates"],
            {"pending_answer_status": "MISSING"},
        )

    def test_old_emvr_theory_links_survive_unrelated_first_field_edit(self) -> None:
        boundary_link = {
            "relation_id": "ELECTROSTATIC_BOUNDARY",
            "supports_design_content": "解释导体边界附近场线方向",
            "supports_design_fields": ["object_constraints"],
        }
        emvr_design = {
            "field_state": {
                "object_constraints": ["导体边界"],
            },
            "structured_requirements": {
                Stage.THEORETICAL_FRAMEWORK.value: {
                    "theory_links": [boundary_link]
                }
            }
        }

        apply_emvr_field_updates(
            emvr_design,
            normalize_emvr_design_update(
                {
                    "field_updates": [
                        {
                            "field_id": "observed_quantities",
                            "operation": "MERGE",
                            "value": ["场线方向"],
                        }
                    ]
                }
            ),
        )

        merged = merge_emvr_structured_requirements(emvr_design)
        self.assertEqual(
            [item["relation_id"] for item in merged["theory_links"]],
            ["ELECTROSTATIC_BOUNDARY"],
        )
        self.assertNotIn("theory_link_state", emvr_design)

    def test_three_field_mixed_emvr_revision_commits_independently(self) -> None:
        research_question = "边界曲率改变时，局部场线和等势面如何重新分布？"
        observation = "记录尖端附近场线密度与等势面间距"
        theory_text = "只采用能解释导体静电边界的高斯定律与边界条件"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": research_question,
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "observations",
                    "operation": "REPLACE",
                    "content": observation,
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "theoretical_framework",
                    "operation": "REPLACE",
                    "content": theory_text,
                    "confidence": 0.99,
                },
            ],
            semantic_updates={
                "emvr_design_update": {
                    "field_updates": [
                        {
                            "field_id": "research_question",
                            "operation": "REPLACE",
                            "value": research_question,
                        },
                        {
                            "field_id": "observed_quantities",
                            "operation": "REPLACE",
                            "value": [observation],
                        },
                        {
                            "field_id": "limitations",
                            "operation": "REPLACE",
                            "value": ["本轮没有要求修改的内容"],
                        },
                    ],
                    "theory_links": [
                        {
                            "relation_id": "ELECTROSTATIC_BOUNDARY",
                            "supports_design_content": "解释导体曲率边界附近的场线方向",
                            "supports_design_fields": [
                                "research_question",
                                "observed_quantities",
                            ],
                        },
                        {
                            "relation_id": "OHMIC_CONDUCTION",
                            "supports_design_content": "只是课程相关",
                            "supports_design_fields": [],
                        },
                    ],
                }
            },
        )
        engine = WorkflowEngine(generator=generator)
        session = variable_stage_session("design_emvr_three_field_revision")
        session.interaction_state = InteractionState.EMVR_DIRECT
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {
                "message": (
                    "改写研究问题，同时更新观察现象，并把理论依据精简为只保留直接相关的关系。"
                )
            },
        )

        self.assertEqual(
            result["stage_payload"]["design_state"]["research_question"],
            research_question,
        )
        self.assertIn(
            observation,
            result["stage_payload"]["stage_design_state"]["observations"],
        )
        saved = engine.store.get(session.design_id)
        merged = merge_emvr_structured_requirements(
            saved.design_context["emvr_design"]
        )
        self.assertNotIn("limitations", merged)
        self.assertEqual(
            [item["relation_id"] for item in merged["theory_links"]],
            ["ELECTROSTATIC_BOUNDARY"],
        )

    def test_correction_then_summary_reads_clean_committed_state_only(self) -> None:
        clean_objective = "解释边界形状如何影响局部场线与等势面分布"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "CORRECT_ASSISTANT",
                    "target": "learning_objective",
                    "operation": "REPLACE",
                    "content": {
                        "error_type": "META_TEXT_CONTAMINATION",
                        "explanation": "删除误写入学习目标的会话说明",
                        "affected_fields": ["learning_objective"],
                        "design_updates": [
                            {
                                "field": "learning_objective",
                                "operation": "REPLACE",
                                "value": clean_objective,
                            }
                        ],
                    },
                    "confidence": 0.99,
                },
                {
                    "type": "REQUEST_SUMMARY",
                    "target": "current_design",
                    "operation": "EXECUTE",
                    "content": None,
                    "confidence": 0.99,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_clean_summary_after_correction")
        ensure_design_state(session)["learning_objective"] = (
            "解释边界条件。另外，我需要确认你是否替换基础比较。"
        )
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "删除学习目标里的确认说明，然后整体看一遍。"},
        )

        self.assertTrue(result["stage_payload"]["read_only_design_summary"])
        self.assertEqual(
            result["stage_payload"]["design_state"]["learning_objective"],
            clean_objective,
        )
        self.assertIn(clean_objective, result["assistant_message"])
        self.assertNotIn("我需要确认", result["assistant_message"])

    def test_completed_idea_review_displays_the_actual_summary(self) -> None:
        session = idea_facet_session("design_visible_idea_review")
        development = session.design_context["idea_development"]
        evidence = {
            "direction_outline": "比较两个点电荷靠近时的场线重排",
            "course_mapping": "静电场、库仑定律与电场叠加",
            "learning_objective": "解释极性和距离为什么改变电场线分布",
            "research_question": "比较同种与异种电荷靠近时场线怎样变化",
            "theoretical_framework": "库仑电场与叠加原理",
            "hypothesis": "距离越近，场线弯曲越明显",
            "conceptual_structure": "两个点电荷、距离调节和场线显示",
        }
        for facet_id, value in evidence.items():
            development["facets"][facet_id].update(
                {"status": "CLEAR", "evidence": value, "source": "STUDENT"}
            )
        development["complete"] = True

        output = build_gap_output(session, "这些内容已经说明清楚")

        self.assertIn("这是目前整理出的实验想法", output.assistant_message)
        self.assertIn("学习目标：解释极性和距离", output.assistant_message)
        self.assertIn("基础比较：", output.assistant_message)
        self.assertIn("基础比较：暂未明确", output.assistant_message)
        self.assertNotIn("同种电荷、异种电荷", output.assistant_message)

    def test_comparison_replace_dialect_commits_explicit_guided_revision(self) -> None:
        message = "基础比较这一项改成规则边界、尖角边界、窄缝边界"
        replacement = ["规则边界", "尖角边界", "窄缝边界"]
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_COMPARISON",
                        "target": "baseline_comparisons",
                        "operation": "REPLACE",
                        "content": {
                            "comparison_id": "electrostatic_material_class_pair",
                            "action": "REPLACE",
                            "cases": replacement,
                        },
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = idea_facet_session("design_guided_comparison_replace_dialect")
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "electrostatic_material_class_pair",
                    "title": "材料类别",
                    "recommended_cases": ["导体情形", "介质情形"],
                    "cases": ["导体情形", "介质情形"],
                    "adoption_status": "PENDING",
                }
            ],
        )
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": message})

        comparisons = result["stage_payload"]["design_state"][
            "baseline_comparisons"
        ]
        self.assertEqual(comparisons[0]["cases"], replacement)
        self.assertEqual(comparisons[0]["adoption_status"], "MODIFIED")
        self.assertIn("规则边界、尖角边界、窄缝边界", result["assistant_message"])
        self.assertNotIn("electrostatic_material_class_pair", result["assistant_message"])

    def test_first_explicit_comparison_replacement_creates_canonical_group(self) -> None:
        message = "基础比较改成曲面完全包住场源、部分包住场源、未包住场源"
        replacement = ["曲面完全包住场源", "部分包住场源", "未包住场源"]
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_COMPARISON",
                        "target": "baseline_comparisons",
                        "operation": "REPLACE",
                        "content": {
                            "comparison_id": "baseline_comparisons",
                            "action": "REPLACE",
                            "cases": replacement,
                            "replace_all": True,
                        },
                        "source_text": message,
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = idea_facet_session("design_first_comparison_group")
        set_baseline_comparisons(session, [])
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": message})

        comparisons = result["stage_payload"]["design_state"][
            "baseline_comparisons"
        ]
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]["cases"], replacement)
        self.assertEqual(comparisons[0]["adoption_status"], "MODIFIED")
        self.assertIn("曲面完全包住场源", result["assistant_message"])

    def test_rejected_model_comparison_creation_is_not_acknowledged_as_saved(self) -> None:
        session = idea_facet_session("design_rejected_comparison_ack")
        session.turn_context = {
            "resolved_intent": resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                resolved_value="补充一个新的基础比较",
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "comparison_updates": [
                        {
                            "comparison_id": "",
                            "action": "CREATE",
                            "new_cases": ["模型虚构且未被学生提出的情形"],
                        }
                    ]
                },
            )
        }

        output = build_gap_output(session, "补充一个新的基础比较")

        self.assertNotIn("新增基础比较", output.assistant_message)
        self.assertNotIn("模型虚构", output.assistant_message)

    def test_complete_stage_answer_advances_without_empty_confirmation_turn(self) -> None:
        class CompletionGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={
                        "pending_answer_status": "CLEAR",
                        "stage_field_updates": [
                            {
                                "field": "independent_variable",
                                "operation": "REPLACE",
                                "value": "两个源之间的距离",
                            },
                            {
                                "field": "observations",
                                "operation": "REPLACE",
                                "value": "电场线和通量",
                            },
                            {
                                "field": "controlled_conditions",
                                "operation": "REPLACE",
                                "value": "电荷量和观察方式",
                            },
                        ],
                    },
                )

            def generate(self, session, user_message):
                return StepOutput(
                    assistant_message="距离作为唯一自变量，场线和通量作为观察量。",
                    stage_payload={
                        "independent_variable": "两个源之间的距离",
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        },
                    },
                    student_task="还可以怎样补充控制条件？",
                )

            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                if pending_action and pending_action.get("type") == "CONFIRM_STAGE_OR_MODIFY":
                    return resolved_intent(
                        UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                        confidence=0.98,
                        source="SEMANTIC_TEST",
                    )
                return super().resolve_intent(
                    session,
                    user_message,
                    pending_action,
                    carried_context,
                )

        engine = WorkflowEngine(generator=CompletionGenerator())
        session = DesignSession(
            design_id="design_stage_confirmation_advance",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={"idea": {"main_direction": "比较两个电荷源"}},
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine.store.save(session)

        advanced = engine.process_turn(
            session.design_id,
            {"message": "拖拽改变两个源的距离，观察电场线和通量"},
        )
        self.assertEqual(
            advanced["transitioned_from_stage"],
            Stage.VARIABLES_AND_CONDITIONS.value,
        )
        self.assertEqual(
            advanced["current_stage"],
            Stage.CONCEPTUAL_PROCEDURE.value,
        )
        self.assertEqual(
            advanced["stage_payload"]["auto_advanced_from_stage"],
            Stage.VARIABLES_AND_CONDITIONS.value,
        )
        self.assertIn("直接接着完善下一项", advanced["assistant_message"])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["type"], "ANSWER_STAGE_QUESTION")
        self.assertEqual(pending["subject"], Stage.CONCEPTUAL_PROCEDURE.value)

    def test_legacy_micro_confirmation_can_advance_after_upgrade(self) -> None:
        generator = ScriptedSemanticGenerator(UserIntent.ACCEPT_PREVIOUS_PROPOSAL)
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_legacy_micro_confirmation",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={
                "idea": {"main_direction": "比较两个电荷源"},
                "guided_stage_drafts": {
                    Stage.VARIABLES_AND_CONDITIONS.value: {
                        "independent_variable": "两个源之间的距离"
                    }
                },
            },
        )
        old_output = StepOutput(
            assistant_message="距离是唯一自变量。",
            stage_payload={"independent_variable": "两个源之间的距离"},
            student_task="你确认一下自变量是否只保留距离。",
        )
        save_pending_action(session, session.current_stage, old_output)
        session.stage_outputs[session.current_stage.value] = {
            **old_output.to_dict(),
            "revision": 1,
        }
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": "合适"})

        self.assertEqual(
            result["transitioned_from_stage"],
            Stage.VARIABLES_AND_CONDITIONS.value,
        )
        self.assertEqual(result["current_stage"], Stage.CONCEPTUAL_PROCEDURE.value)

    def test_guided_entry_uses_structured_facts_not_conversation_commands(self) -> None:
        session = DesignSession(
            design_id="design_structured_entry_basis",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.CONCEPTUAL_PROCEDURE),
            design_context={
                "idea": {"main_direction": "比较两个电荷源靠近时的场分布"},
                "student_decisions": {
                    Stage.VARIABLES_AND_CONDITIONS.value: [
                        {
                            "message": "保留全部环节。改变距离并观察场线",
                            "before_revision": 1,
                        },
                        {"message": "确认", "before_revision": 2},
                    ]
                },
                "guided_stage_drafts": {
                    Stage.VARIABLES_AND_CONDITIONS.value: {
                        "independent_variable": "两个源之间的距离",
                        "observations": ["电场线", "中间平面通量"],
                        "controlled_variables": ["电荷量"],
                    }
                },
            },
        )

        output = guided_stage_entry_output(session)

        self.assertNotIn("保留全部环节", output.assistant_message)
        self.assertNotIn("设计依据包括：确认", output.assistant_message)
        self.assertIn("两个源之间的距离", output.assistant_message)
        self.assertIn("中间平面通量", output.assistant_message)
        self.assertNotIn("你可以直接说明哪些环节", output.assistant_message)

    def test_request_for_possible_result_is_reference_not_student_design_fact(self) -> None:
        class ReferenceGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(UserIntent.REQUEST_MORE_EXAMPLES)

            def generate(self, session, user_message):
                return StepOutput(
                    assistant_message=(
                        "一种可修改的理论参考是：异种电荷条件下，中间平面通量可能随距离减小而增大；"
                        "这只是理论预测，不是实测结果。"
                    ),
                    stage_payload={
                        "reference_prediction": "通量可能随距离减小而增大",
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        },
                    },
                    student_task="你可以检查这个参考是否符合前面确定的物理关系。",
                    visualization={"data_kind": "theoretical_prediction"},
                )

        engine = WorkflowEngine(generator=ReferenceGenerator())
        session = DesignSession(
            design_id="design_reference_result_request",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.EXPECTED_DATA_VISUALIZATION),
            design_context={"idea": {"main_direction": "比较电荷源距离与通量"}},
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "不知道，请给出你认为的一种可能"},
        )

        self.assertIn("一种可修改的理论参考", result["assistant_message"])
        self.assertNotIn("不知道，请给出", str(result["stage_payload"]))
        self.assertTrue(result["stage_payload"]["stage_ready_for_confirmation"])
        stored = engine.get_design(session.design_id)["design_context"]
        stage_decisions = stored.get("student_decisions", {}).get(
            Stage.EXPECTED_DATA_VISUALIZATION.value,
            [],
        )
        self.assertEqual(stage_decisions, [])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["type"], "CONFIRM_STAGE_OR_MODIFY")

    def test_rule_based_fallback_answers_reference_request_from_carried_context(self) -> None:
        generator = ScriptedSemanticGenerator(UserIntent.REQUEST_MORE_EXAMPLES)
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_fallback_reference_request",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.EXPECTED_DATA_VISUALIZATION),
            design_context={
                "idea": {"main_direction": "比较两个场源靠近时的场分布"},
                "guided_stage_drafts": {
                    Stage.VARIABLES_AND_CONDITIONS.value: {
                        "independent_variable": "两个源之间的距离",
                        "observations": ["电场线", "中间区域场强"],
                    }
                },
            },
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "我还不确定，你先给一个你认为合适的显示方式"},
        )

        self.assertIn("能直接拿来改的理论参考", result["assistant_message"])
        self.assertIn("两个源之间的距离", result["assistant_message"])
        self.assertNotIn("我还不确定", str(result["stage_payload"]))
        self.assertTrue(result["stage_payload"]["reference_only"])
        self.assertEqual(result["stage_payload"]["stage_readiness"]["ready_for_confirmation"], False)
        stored = engine.get_design(session.design_id)["design_context"]
        self.assertEqual(
            stored.get("student_decisions", {}).get(
                Stage.EXPECTED_DATA_VISUALIZATION.value,
                [],
            ),
            [],
        )

    def test_accepted_limitations_reference_can_advance_without_field_disconnect(self) -> None:
        class LimitReferenceGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(UserIntent.REQUEST_MORE_EXAMPLES)

            def resolve_intent(self, session, user_message, pending_action, carried_context):
                if self.intent is UserIntent.ACCEPT_PREVIOUS_PROPOSAL:
                    return resolved_intent(
                        self.intent,
                        target=str(pending_action.get("subject") or ""),
                        advance_requested=True,
                        confidence=0.98,
                        source="SEMANTIC_TEST",
                    )
                return super().resolve_intent(
                    session,
                    user_message,
                    pending_action,
                    carried_context,
                )

        generator = LimitReferenceGenerator()
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_limit_reference_accept",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.DESIGN_VALUE_AND_LIMITATIONS),
            design_context={
                "idea": {"main_direction": "比较两个场源靠近时的场分布"},
                "idea_development": {
                    "facets": {
                        "learning_objective": {
                            "status": "CLEAR",
                            "evidence": "解释源间距离如何改变电场分布",
                        }
                    }
                },
            },
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine.store.save(session)

        reference = engine.process_turn(
            session.design_id,
            {"message": "我没想到限制，你先举一个贴合当前设计的参考"},
        )
        self.assertIn("这里不用再重复一遍", reference["assistant_message"])
        self.assertTrue(reference["stage_payload"]["limitations"])

        generator.intent = UserIntent.ACCEPT_PREVIOUS_PROPOSAL
        advanced = engine.process_turn(
            session.design_id,
            {"message": "这些都符合我的想法，可以继续"},
        )
        self.assertIsNone(advanced["completion_error"])
        self.assertEqual(
            advanced["current_stage"],
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT.value,
        )

    def test_one_complete_student_summary_finishes_without_second_confirmation(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={"pending_answer_status": "CLEAR"},
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_single_summary_completion",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT),
            design_context={"idea": {"main_direction": "比较点电荷距离与电场分布"}},
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine.store.save(session)

        summary = (
            "我想比较同种和异种点电荷在距离变化时的电场线分布，观察中间区域场强的差异，"
            "并用ECE329中的静电场叠加关系解释这些现象。"
        )
        completed = engine.process_turn(session.design_id, {"message": summary})
        self.assertTrue(completed["stage_payload"]["student_summary_received"])
        self.assertTrue(completed["stage_payload"]["student_summary_confirmed"])
        self.assertNotIn("请先用两到三句话", completed["assistant_message"])
        self.assertNotIn("确认完成", completed.get("student_task") or "")
        self.assertEqual(completed["workflow_status"], "complete")
        self.assertIsNone(completed["completion_error"])
        self.assertIn("到这里就完成了", completed["assistant_message"])

    def test_public_final_stage_answer_act_is_saved_as_student_summary(self) -> None:
        summary = (
            "我设计的实验比较两种电荷配置在距离减小时的场线变化，"
            "并用静电场叠加关系解释中间区域的差异。"
        )
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT.value,
                    "operation": "REPLACE",
                    "content": summary,
                    "confidence": 0.99,
                }
            ],
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_structured_final_summary",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(
                Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            ),
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        engine.store.save(session)

        completed = engine.process_turn(session.design_id, {"message": summary})

        self.assertEqual(completed["workflow_status"], "complete")
        stored = engine.store.get(session.design_id)
        self.assertEqual(
            stage_design_state_snapshot(stored)["student_summary"],
            summary,
        )
        self.assertEqual(
            stored.design_context["synthesis"]["student_summary"],
            summary,
        )

    def test_final_stage_correction_recovers_previous_summary_without_reasking(self) -> None:
        summary = (
            "我设计的实验比较距离变化时同种与异种电荷的场线分布，"
            "并通过中间区域的变化检验静电场叠加关系。"
        )
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "CORRECT_ASSISTANT",
                    "target": "student_summary",
                    "operation": "MERGE",
                    "content": {
                        "error_type": "IGNORED_PRIOR_ANSWER",
                        "explanation": "上一轮已经给出了完整总结。",
                        "affected_fields": ["student_summary"],
                    },
                    "confidence": 0.99,
                }
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="design_recover_final_summary",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(
                Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT
            ),
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.model_context["dialogue_state"]["pending_action"][
            "candidate_answer"
        ] = summary
        engine.store.save(session)

        completed = engine.process_turn(
            session.design_id,
            {"message": "我上一轮已经完成总结了。"},
        )

        self.assertEqual(completed["workflow_status"], "complete")
        self.assertTrue(
            completed["stage_payload"]["summary_recovered_from_previous_turn"]
        )
        self.assertNotIn("请告诉我具体要改哪一项", completed["assistant_message"])
        self.assertEqual(
            engine.store.get(session.design_id).design_context["synthesis"][
                "student_summary"
            ],
            summary,
        )

    def test_guided_complete_sections_auto_advance_only_without_open_work(self) -> None:
        cases = {
            Stage.VARIABLES_AND_CONDITIONS: (
                {
                    "independent_variable": "距离",
                    "observations": "场线和通量",
                    "controlled_conditions": "电荷量和观察方式",
                },
                None,
            ),
            Stage.CONCEPTUAL_PROCEDURE: (
                {"procedure_steps": ["建立基准", "改变距离", "记录结果"]},
                None,
            ),
            Stage.EXPECTED_DATA_VISUALIZATION: (
                {"visualization_plan": "同步显示场线和距离读数"},
                {"data_kind": "theoretical_prediction"},
            ),
            Stage.RESULT_INTERPRETATION: (
                {"result_interpretation": "比较结果是否支持叠加预测"},
                None,
            ),
            Stage.DESIGN_VALUE_AND_LIMITATIONS: (
                {"limitations": "点电荷近似在过近距离时失效"},
                None,
            ),
        }
        for stage, (payload, visualization) in cases.items():
            with self.subTest(stage=stage.value):
                session = DesignSession(
                    design_id=f"auto_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=list(Stage).index(stage),
                )
                apply_stage_field_updates(
                    session,
                    [
                        {
                            "field": field,
                            "operation": "REPLACE",
                            "value": value,
                        }
                        for field, value in payload.items()
                    ],
                    stage=stage,
                )
                output = StepOutput(
                    assistant_message="这一部分已经整理清楚。",
                    stage_payload={
                        **payload,
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        },
                    },
                    visualization=visualization,
                )
                pending = {
                    "type": "ANSWER_STAGE_QUESTION",
                    "subject": stage.value,
                }
                clear_updates = {"pending_answer_status": "CLEAR"}
                self.assertTrue(
                    _guided_stage_should_auto_advance(
                        session,
                        stage,
                        output,
                        pending,
                        UserIntent.ANSWER_CURRENT_QUESTION.value,
                        clear_updates,
                    )
                )
                self.assertFalse(
                    _guided_stage_should_auto_advance(
                        session,
                        stage,
                        output,
                        pending,
                        UserIntent.ANSWER_CURRENT_QUESTION.value,
                        {
                            **clear_updates,
                            "unresolved_content": ["还有一部分未理解"],
                        },
                    )
                )

        incomplete = DesignSession(
            design_id="auto_incomplete_variables",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
        )
        apply_stage_field_updates(
            incomplete,
            [
                {
                    "field": "independent_variable",
                    "operation": "REPLACE",
                    "value": "距离",
                }
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        self.assertFalse(
            _guided_stage_should_auto_advance(
                incomplete,
                Stage.VARIABLES_AND_CONDITIONS,
                StepOutput(
                    assistant_message="只有自变量已经明确。",
                    stage_payload={
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        }
                    },
                ),
                {
                    "type": "ANSWER_STAGE_QUESTION",
                    "subject": Stage.VARIABLES_AND_CONDITIONS.value,
                },
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                {"pending_answer_status": "CLEAR"},
            )
        )

    def test_every_complete_guided_section_enters_next_meaningful_question(self) -> None:
        cases = {
            Stage.VARIABLES_AND_CONDITIONS: {
                "independent_variable": "距离",
                "observations": "场线和通量",
                "controlled_conditions": "电荷量和观察方式",
            },
            Stage.CONCEPTUAL_PROCEDURE: {
                "procedure_steps": "建立基准、改变距离、记录并比较",
            },
            Stage.EXPECTED_DATA_VISUALIZATION: {
                "visualization_plan": "同步显示场线、颜色和距离读数",
            },
            Stage.RESULT_INTERPRETATION: {
                "result_interpretation": "比较结果是否支持原有趋势，并检查偏差来源",
            },
            Stage.DESIGN_VALUE_AND_LIMITATIONS: {
                "limitations": "点电荷近似与显示分辨率限制",
            },
        }

        class CompleteSectionGenerator(ScriptedSemanticGenerator):
            def __init__(self, values: dict[str, str]) -> None:
                self.values = values
                super().__init__(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={
                        "pending_answer_status": "CLEAR",
                        "stage_field_updates": [
                            {
                                "field": field,
                                "operation": "REPLACE",
                                "value": value,
                            }
                            for field, value in values.items()
                        ],
                    },
                )

            def generate(self, session, user_message):
                return StepOutput(
                    assistant_message="你的说明已经把这一部分补充完整。",
                    stage_payload={
                        **self.values,
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        },
                    },
                    student_task="请确认后继续。",
                    visualization=(
                        {"data_kind": "theoretical_prediction"}
                        if session.current_stage
                        is Stage.EXPECTED_DATA_VISUALIZATION
                        else None
                    ),
                )

        stages = list(Stage)
        for stage, values in cases.items():
            with self.subTest(stage=stage.value):
                engine = WorkflowEngine(generator=CompleteSectionGenerator(values))
                session = DesignSession(
                    design_id=f"auto_engine_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=stages.index(stage),
                )
                entry = guided_stage_entry_output(session)
                save_pending_action(session, stage, entry)
                session.stage_outputs[stage.value] = {
                    "assistant_message": entry.assistant_message,
                    "stage_payload": entry.stage_payload,
                }
                engine.store.save(session)

                result = engine.process_turn(
                    session.design_id,
                    {"message": "这是我对这一部分的完整说明。"},
                )

                next_stage = stages[stages.index(stage) + 1]
                self.assertEqual(result["transitioned_from_stage"], stage.value)
                self.assertEqual(result["current_stage"], next_stage.value)
                self.assertIn("直接接着完善下一项", result["assistant_message"])
                pending = current_pending_action(engine.store.get(session.design_id))
                assert pending is not None
                self.assertEqual(pending["subject"], next_stage.value)

    def test_context_dependent_short_acceptance_uses_semantic_resolution(self) -> None:
        open_question = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.CONCEPTUAL_PROCEDURE.value,
            "proposal": {"reference_draft": ["建立基准", "改变条件"]},
            "question": "你认为流程需要哪些环节？",
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        self.assertIsNone(deterministic_intent("保留", open_question))
        self.assertIsNone(deterministic_intent("确认", open_question))

        open_question["candidate_answer"] = "建立基准后改变条件并记录结果"
        self.assertIsNone(deterministic_intent("确认", open_question))

        proposal_confirmation = {
            "type": "CONFIRM_OR_MODIFY",
            "subject": "experiment_idea_outline",
            "proposal": {"complete": True},
            "allowed_intents": [UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value],
        }
        self.assertIsNone(deterministic_intent("保留", proposal_confirmation))
        explicit_ui_event = deterministic_intent(
            "按钮显示文字不参与判断",
            proposal_confirmation,
            complete_stage=True,
        )
        assert explicit_ui_event is not None
        self.assertEqual(explicit_ui_event["intent"], UserIntent.ADVANCE_STAGE.value)

        mode_event = deterministic_intent(
            "按钮文字可以任意变化",
            proposal_confirmation,
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        assert mode_event is not None
        self.assertEqual(
            mode_event["intent"],
            UserIntent.SET_INTERACTION_STATE.value,
        )
        self.assertEqual(
            mode_event["semantic_updates"]["interaction_state_request"],
            InteractionState.EMVR_DIRECT.value,
        )

    def test_emvr_offline_revision_fallback_does_not_change_guided_mode(self) -> None:
        pending = {
            "type": "CONFIRM_STAGE_OR_MODIFY",
            "subject": Stage.LEARNING_OBJECTIVES.value,
            "proposal": {"conceptual_objective": "解释材料边界对电场的影响"},
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            ],
        }
        message = "我想补充对导体与介质边界响应的比较"

        guided = fallback_intent(
            message,
            pending,
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        emvr = fallback_intent(
            message,
            pending,
            interaction_state=InteractionState.EMVR_DIRECT,
        )

        self.assertEqual(
            guided["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
        )
        self.assertEqual(
            emvr["intent"], UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
        )
        self.assertEqual(guided["resolved_value"], message)
        self.assertEqual(emvr["resolved_value"], message)

    def test_semantic_mode_and_course_scope_are_schema_validated(self) -> None:
        validated = validate_resolved_intent(
            resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "interaction_state_request": "EMVR_DIRECT",
                    "course_scope_status": "COURSE_CONTENT",
                },
            ),
            None,
        )
        self.assertEqual(
            validated["semantic_updates"]["interaction_state_request"],
            "EMVR_DIRECT",
        )
        self.assertEqual(
            validated["semantic_updates"]["course_scope_status"],
            "COURSE_CONTENT",
        )

        rejected = validate_resolved_intent(
            resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "interaction_state_request": "ARBITRARY_MODE",
                    "course_scope_status": "ARBITRARY_SCOPE",
                },
            ),
            None,
        )
        self.assertIsNone(
            rejected["semantic_updates"]["interaction_state_request"]
        )
        self.assertEqual(
            rejected["semantic_updates"]["course_scope_status"],
            "UNCERTAIN",
        )

    def test_candidate_confirmation_closes_every_stage_one_student_facet(self) -> None:
        student_facets = (
            "learning_objective",
            "research_question",
            "hypothesis",
            "conceptual_structure",
        )

        for facet_id in student_facets:
            with self.subTest(facet_id=facet_id):
                class FacetConfirmationGenerator(RuleBasedStageGenerator):
                    def __init__(self) -> None:
                        self.intent_calls = 0

                    def resolve_intent(
                        self,
                        session,
                        user_message,
                        pending_action,
                        carried_context,
                    ):
                        self.intent_calls += 1
                        if self.intent_calls == 1:
                            return resolved_intent(
                                UserIntent.UNCLEAR,
                                target=facet_id,
                                confidence=0.96,
                                source="SEMANTIC_TEST",
                                semantic_updates={},
                            )
                        return resolved_intent(
                            UserIntent.ANSWER_CURRENT_QUESTION,
                            target=facet_id,
                            resolved_value=str(
                                pending_action.get("candidate_answer") or ""
                            ),
                            confidence=0.98,
                            source="SEMANTIC_TEST",
                            semantic_updates={
                                "facet_updates": [
                                    {
                                        "facet_id": facet_id,
                                        "status": "CLEAR",
                                        "operation": "REPLACE",
                                        "value": str(
                                            pending_action.get("candidate_answer") or ""
                                        ),
                                    }
                                ]
                            },
                        )

                generator = FacetConfirmationGenerator()
                engine = WorkflowEngine(generator=generator)
                session = idea_facet_session(f"design_confirm_{facet_id}")
                development = session.design_context["idea_development"]
                for current_id, facet in development["facets"].items():
                    if current_id in student_facets:
                        facet.update(
                            {
                                "status": "MISSING" if current_id == facet_id else "CLEAR",
                                "evidence": "" if current_id == facet_id else "已明确",
                                "source": None if current_id == facet_id else "TEST",
                            }
                        )
                development["active_facet_id"] = facet_id
                development["missing_facet_ids"] = [facet_id]
                development["completed_facet_ids"] = [
                    current_id
                    for current_id, facet in development["facets"].items()
                    if facet["status"] == "CLEAR"
                ]
                development["complete"] = False
                save_pending_action(
                    session,
                    Stage.IDEA_BRAINSTORMING,
                    build_gap_output(session, ""),
                )
                engine.store.save(session)
                candidate = f"这是学生针对{facet_id}给出的完整说明"

                ambiguous = engine.process_turn(
                    session.design_id,
                    {"message": candidate},
                )
                self.assertTrue(
                    ambiguous["stage_payload"]["clarification_required"]
                )
                confirmed = engine.process_turn(
                    session.design_id,
                    {"message": "刚才那段就是我对这一问的回答"},
                )

                status = confirmed["stage_payload"]["idea_development_status"]
                self.assertEqual(
                    status["facets_by_id"][facet_id]["status"],
                    "CLEAR",
                )
                self.assertEqual(
                    status["facets_by_id"][facet_id]["evidence"],
                    candidate,
                )
                self.assertFalse(
                    confirmed["stage_payload"].get("clarification_required", False)
                )

    def test_candidate_confirmation_closes_every_later_guided_stage_question(self) -> None:
        guided_stages = (
            Stage.VARIABLES_AND_CONDITIONS,
            Stage.CONCEPTUAL_PROCEDURE,
            Stage.EXPECTED_DATA_VISUALIZATION,
            Stage.RESULT_INTERPRETATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        )

        for stage in guided_stages:
            with self.subTest(stage=stage.value):
                class StageConfirmationGenerator(RuleBasedStageGenerator):
                    def __init__(self) -> None:
                        self.intent_calls = 0
                        self.generated_messages: list[str] = []

                    def resolve_intent(
                        self,
                        session,
                        user_message,
                        pending_action,
                        carried_context,
                    ):
                        self.intent_calls += 1
                        if self.intent_calls == 1:
                            return resolved_intent(
                                UserIntent.UNCLEAR,
                                target=stage.value,
                                confidence=0.96,
                                source="SEMANTIC_TEST",
                                semantic_updates={},
                            )
                        return resolved_intent(
                            UserIntent.ANSWER_CURRENT_QUESTION,
                            target=stage.value,
                            resolved_value=str(
                                pending_action.get("candidate_answer") or ""
                            ),
                            confidence=0.98,
                            source="SEMANTIC_TEST",
                            semantic_updates={"pending_answer_status": "CLEAR"},
                        )

                    def generate(self, session, user_message):
                        self.generated_messages.append(user_message)
                        return StepOutput(
                            assistant_message="我已经沿用这段回答继续整理。",
                            stage_payload={"student_stage_answer": user_message},
                            student_task=None,
                        )

                generator = StageConfirmationGenerator()
                engine = WorkflowEngine(generator=generator)
                session = DesignSession(
                    design_id=f"design_confirm_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=list(Stage).index(stage),
                    design_context={"idea": {"main_direction": "比较静电场分布"}},
                )
                entry = guided_stage_entry_output(session)
                save_pending_action(session, stage, entry)
                session.stage_outputs[stage.value] = {
                    "stage_payload": entry.stage_payload,
                    "assistant_message": entry.assistant_message,
                }
                engine.store.save(session)
                candidate = "这是我对这一部分提供的具体说明，其中包含明确的物理判断"

                ambiguous = engine.process_turn(
                    session.design_id,
                    {"message": candidate},
                )
                self.assertTrue(
                    ambiguous["stage_payload"]["clarification_required"]
                )
                confirmed = engine.process_turn(
                    session.design_id,
                    {"message": "沿用我上一条的说明"},
                )

                self.assertFalse(
                    confirmed["stage_payload"].get("clarification_required", False)
                )
                if stage is Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT:
                    self.assertEqual(generator.generated_messages, [])
                    self.assertTrue(
                        confirmed["stage_payload"]["student_summary_received"]
                    )
                    stored = engine.store.get(session.design_id)
                    self.assertEqual(
                        stored.design_context["synthesis"]["student_summary"],
                        candidate,
                    )
                else:
                    self.assertEqual(generator.generated_messages, [candidate])
                    self.assertEqual(
                        confirmed["stage_payload"]["student_stage_answer"],
                        candidate,
                    )

    def test_every_guided_public_stage_uses_structured_answer_status(self) -> None:
        guided_stages = (
            Stage.VARIABLES_AND_CONDITIONS,
            Stage.CONCEPTUAL_PROCEDURE,
            Stage.EXPECTED_DATA_VISUALIZATION,
            Stage.RESULT_INTERPRETATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT,
        )
        for stage in guided_stages:
            with self.subTest(stage=stage.value):
                session = DesignSession(
                    design_id=f"design_pending_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=list(Stage).index(stage),
                    design_context={"idea": {"main_direction": "静电场比较"}},
                )
                output = guided_stage_entry_output(session)
                pending = save_pending_action(session, stage, output)
                assert pending is not None
                self.assertEqual(pending["type"], "ANSWER_STAGE_QUESTION")
                self.assertEqual(pending["subject"], stage.value)

                valid = validate_resolved_intent(
                    resolved_intent(
                        UserIntent.ANSWER_CURRENT_QUESTION,
                        confidence=0.96,
                        source="SEMANTIC_TEST",
                        semantic_updates={"pending_answer_status": "CLEAR"},
                    ),
                    pending,
                )
                omitted = validate_resolved_intent(
                    resolved_intent(
                        UserIntent.ANSWER_CURRENT_QUESTION,
                        confidence=0.96,
                        source="SEMANTIC_TEST",
                        semantic_updates={},
                    ),
                    pending,
                )

                self.assertEqual(
                    valid["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
                )
                self.assertEqual(
                    omitted["intent"],
                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                )
                self.assertEqual(
                    omitted["semantic_updates"]["pending_answer_status"],
                    "CLEAR",
                )

    def test_later_stage_repeated_question_is_removed_after_valid_answer(self) -> None:
        class RepeatingQuestionGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={"pending_answer_status": "CLEAR"},
                )

            def generate(self, session, user_message):
                pending = session.turn_context["pending_action"]
                return StepOutput(
                    assistant_message="我先继续整理变量。",
                    stage_payload={"independent_variable": user_message},
                    student_task=str(pending["question"]),
                )

        session = DesignSession(
            design_id="design_later_stage_repeat_guard",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={"idea": {"main_direction": "比较两个场源"}},
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine = WorkflowEngine(generator=RepeatingQuestionGenerator())
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "主动改变两个场源之间的距离，并观察电场线分布"},
        )

        self.assertTrue(result["stage_payload"]["repeated_question_avoided"])
        self.assertIsNone(result["student_task"])
        self.assertIn("这一问不再重复", result["assistant_message"])

    def test_emvr_repeated_question_is_removed_after_valid_answer(self) -> None:
        question = "请说明这个VR实验中主要改变和观察的物理量。"
        output = StepOutput(
            assistant_message="已整理当前变量草稿。",
            stage_payload={"independent_variable": "两个场源之间的距离"},
            student_task="请说明这个VR实验中主要改变、观察的物理量。",
        )
        pending = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "subject": Stage.VARIABLES_AND_CONDITIONS.value,
            "question": question,
        }

        _remove_repeated_guided_question(
            output,
            pending,
            "改变两个场源之间的距离，并观察场线分布",
            InteractionState.EMVR_DIRECT,
        )

        self.assertTrue(output.stage_payload["repeated_question_avoided"])
        self.assertIsNone(output.student_task)
        self.assertIn("同一问题不再重复", output.assistant_message)

    def test_long_answer_that_quotes_previous_question_keeps_useful_response(self) -> None:
        question = "你想研究什么现象，以及它为什么值得观察？"
        assistant = (
            "你刚才回答了‘你想研究什么现象，以及它为什么值得观察’。"
            "我已经据此整理出导体与介质的研究对象、课程关系和观察重点，"
            "下面继续展示实验大纲雏形。"
        )
        output = StepOutput(
            assistant_message=assistant,
            stage_payload={"experiment_outline_seed": {"core_phenomenon": "材料边界"}},
            student_task="接下来请写出一个可回答的研究问题。",
        )

        _remove_repeated_guided_question(
            output,
            {
                "type": "ANSWER_STAGE_QUESTION",
                "subject": Stage.IDEA_BRAINSTORMING.value,
                "question": question,
            },
            "比较导体与介质周围的场线分布",
        )

        self.assertEqual(output.assistant_message, assistant)
        self.assertNotIn("repeated_question_avoided", output.stage_payload)

    def test_repeated_unparsed_answer_recovers_the_exact_pending_facet(self) -> None:
        class AlwaysUnclearGenerator(RuleBasedStageGenerator):
            def resolve_intent(self, session, user_message, pending_action, carried_context):
                return resolved_intent(
                    UserIntent.UNCLEAR,
                    target=str(pending_action.get("subject") or "") if pending_action else None,
                    confidence=0.96,
                    source="SEMANTIC_TEST",
                )

        engine = WorkflowEngine(generator=AlwaysUnclearGenerator())
        session = idea_facet_session("design_repeated_candidate_recovery")
        engine.store.save(session)
        answer = "比较导体和介质在同样外加电场下，场线的弯曲形态和分布有什么不同。"

        first = engine.process_turn(session.design_id, {"message": answer})
        self.assertTrue(first["stage_payload"]["clarification_required"])
        self.assertIn("收到了你的回答", first["assistant_message"])

        second = engine.process_turn(
            session.design_id,
            {"message": f"我想比较的是：{answer}"},
        )

        facet = second["stage_payload"]["idea_development_status"][
            "facets_by_id"
        ]["research_question"]
        self.assertEqual(facet["status"], "CLEAR")
        self.assertEqual(facet["evidence"], answer)
        self.assertNotIn("没有写入", second["assistant_message"])
        self.assertEqual(
            current_pending_action(engine.store.get(session.design_id))["subject"],
            "learning_objective",
        )

    def test_contextual_advance_commits_retained_guided_answer_before_moving_on(self) -> None:
        session = idea_facet_session("design_retained_answer_advance")
        pending = current_pending_action(session)
        assert pending is not None
        answer = "比较导体与介质在相同外加电场下的场线弯曲和空间分布"
        retained = record_pending_clarification(session, answer)
        assert retained is not None
        self.assertFalse(retained["candidate_binding_authorized"])

        recovered = recover_repeated_pending_answer(
            resolved_intent(
                UserIntent.ADVANCE_STAGE,
                confidence=0.97,
                dialogue_acts=[
                    {
                        "type": "CONTROL",
                        "target": "ADVANCE",
                        "operation": "MERGE",
                        "content": None,
                        "confidence": 0.97,
                    }
                ],
                actions_authoritative=True,
                advance_requested=True,
            ),
            retained,
            "继续往下整理",
        )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["resolved_value"], answer)
        self.assertTrue(recovered["advance_requested"])
        self.assertEqual(
            [item["type"] for item in recovered["dialogue_acts"]],
            ["ANSWER_PENDING_QUESTION", "CONTROL"],
        )

    def test_unparsed_pending_candidate_has_no_direct_confirmation_action(self) -> None:
        session = idea_facet_session("design_pending_candidate_ui_action")
        candidate = "比较同种与异种电荷靠近时，场线如何随距离变化"
        pending = record_pending_clarification(session, candidate)
        assert pending is not None

        output = clarification_output(pending)
        choices = output.stage_payload["clarification_choices"]
        self.assertEqual(choices, [])
        self.assertFalse(pending["candidate_binding_authorized"])
        self.assertIn("没有改动现有设计", output.assistant_message)

    def test_retained_candidate_recovery_applies_to_later_guided_single_field(self) -> None:
        candidate = "建立基准状态，逐步改变距离，记录场线后比较两种材料条件"
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "interaction_state": InteractionState.GUIDED_DESIGN.value,
            "subject": "procedure_steps",
            "answer_fields": ["procedure_steps"],
            "question": "你认为实验需要哪些关键环节？",
            "candidate_answer": candidate,
            "candidate_binding_authorized": False,
        }

        recovered = recover_repeated_pending_answer(
            resolved_intent(
                UserIntent.ADVANCE_STAGE,
                confidence=0.96,
                advance_requested=True,
            ),
            pending,
            "继续后面的内容",
        )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["dialogue_acts"][0]["target"], "procedure_steps")
        self.assertEqual(recovered["dialogue_acts"][0]["content"], candidate)

    def test_retained_emvr_candidate_requires_semantic_field_split(self) -> None:
        candidate = "拖动两个带电球改变距离，并观察场线弯曲和重排"
        pending = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "answer_fields": ["research_object", "interactions", "observations"],
            "candidate_answer": candidate,
            "candidate_binding_authorized": False,
        }

        recovered = recover_repeated_pending_answer(
            resolved_intent(
                UserIntent.ADVANCE_STAGE,
                confidence=0.96,
                advance_requested=True,
            ),
            pending,
            "继续后面的内容",
        )

        self.assertIsNone(recovered)

    def test_pending_action_mode_is_owned_by_state_machine_in_both_modes(self) -> None:
        for mode, expected_type in (
            (InteractionState.GUIDED_DESIGN, "ANSWER_STAGE_QUESTION"),
            (InteractionState.EMVR_DIRECT, "ANSWER_EMVR_STAGE_QUESTION"),
        ):
            with self.subTest(mode=mode.value):
                session = DesignSession(
                    design_id=f"design_pending_mode_{mode.value}",
                    interaction_state=mode,
                    current_stage_index=7,
                )
                output = StepOutput(
                    assistant_message="请说明准备改变和观察哪些量。",
                    stage_payload={},
                    student_task="请说明准备改变和观察哪些量。",
                )

                saved = save_pending_action(
                    session,
                    Stage.VARIABLES_AND_CONDITIONS,
                    output,
                )

                self.assertIsNotNone(saved)
                assert saved is not None
                self.assertEqual(saved["type"], expected_type)
                self.assertEqual(saved["interaction_state"], mode.value)

    def test_legacy_emvr_marker_blocks_guided_candidate_recovery(self) -> None:
        candidate = "改变距离并观察场线变化"
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": "procedure_steps",
            "answer_fields": ["procedure_steps"],
            "candidate_answer": candidate,
            "candidate_binding_authorized": False,
        }

        recovered = recover_repeated_pending_answer(
            resolved_intent(UserIntent.UNCLEAR, confidence=0.96),
            pending,
            candidate,
        )

        self.assertIsNone(recovered)

    def test_reload_migrates_legacy_pending_type_to_current_mode(self) -> None:
        session = DesignSession(
            design_id="design_legacy_emvr_pending_mode",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=7,
            model_context={
                "dialogue_state": {
                    "pending_action": {
                        "type": "ANSWER_STAGE_QUESTION",
                        "subject": "procedure_steps",
                        "answer_fields": ["procedure_steps"],
                        "question": "请说明实验流程。",
                        "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
                    }
                }
            },
        )

        hydrated = hydrate_pending_action_from_history(session)

        self.assertIsNotNone(hydrated)
        assert hydrated is not None
        self.assertEqual(hydrated["type"], "ANSWER_EMVR_STAGE_QUESTION")
        self.assertEqual(
            hydrated["interaction_state"],
            InteractionState.EMVR_DIRECT.value,
        )

    def test_pending_confirmation_action_applies_to_every_open_question_type(self) -> None:
        for pending_type, interaction_state in (
            ("ANSWER_STAGE_QUESTION", InteractionState.GUIDED_DESIGN),
            ("ANSWER_EMVR_STAGE_QUESTION", InteractionState.EMVR_DIRECT),
        ):
            with self.subTest(pending_type=pending_type):
                pending = {
                    "action_id": f"action_{pending_type}",
                    "type": pending_type,
                    "subject": Stage.VARIABLES_AND_CONDITIONS.value,
                    "question": "请补充当前设计项。",
                    "proposal": {"stage": Stage.VARIABLES_AND_CONDITIONS.value},
                    "candidate_answer": "改变距离，观察场线，并保持源强不变",
                    "candidate_binding_authorized": True,
                    "interaction_state": interaction_state.value,
                    "allowed_intents": [
                        UserIntent.ANSWER_CURRENT_QUESTION.value,
                        UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                        UserIntent.REQUEST_MORE_EXAMPLES.value,
                        UserIntent.UNCLEAR.value,
                    ],
                }
                output = clarification_output(pending, interaction_state)
                accept = next(
                    item
                    for item in output.stage_payload["clarification_choices"]
                    if item["option_id"].startswith("pending_accept::")
                )
                resolved = deterministic_intent(
                    accept["label"],
                    pending,
                    selected_option_id=accept["option_id"],
                )
                assert resolved is not None
                validated = validate_resolved_intent(resolved, pending)

                self.assertEqual(
                    validated["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
                )
                self.assertEqual(
                    validated["semantic_updates"]["pending_answer_status"],
                    "CLEAR",
                )

    def test_stale_pending_action_id_cannot_become_design_content(self) -> None:
        session = idea_facet_session("design_stale_pending_ui_action")
        pending = record_pending_clarification(
            session,
            "比较两种电荷配置下的场线变化",
        )
        assert pending is not None

        resolved = deterministic_intent(
            "沿用刚才的表述",
            pending,
            selected_option_id="pending_accept::action_from_old_turn",
        )

        assert resolved is not None
        self.assertEqual(resolved["intent"], UserIntent.UNCLEAR.value)
        self.assertIsNone(resolved["resolved_value"])

    def test_unresolved_candidate_turns_preserve_revision_sequence(self) -> None:
        session = idea_facet_session("design_pending_candidate_history")
        original = "比较同种与异种电荷靠近时的场线变化"
        revision = "比较同种与异种电荷靠近过程中，场线如何随距离变化"

        first = record_pending_clarification(session, original)
        second = record_pending_clarification(session, revision)

        assert first is not None and second is not None
        self.assertEqual(second["candidate_answer"], original)
        self.assertEqual(second["candidate_turns"], [original, revision])
        self.assertNotEqual(
            clarification_output(first).assistant_message,
            clarification_output(second).assistant_message,
        )

    def test_long_initial_course_idea_becomes_direction_detail_without_scene_replay(self) -> None:
        class CourseAnswerGenerator(RuleBasedStageGenerator):
            def resolve_intent(self, session, user_message, pending_action, carried_context):
                return resolved_intent(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    target=Stage.IDEA_BRAINSTORMING.value,
                    resolved_value=user_message,
                    confidence=0.97,
                    source="SEMANTIC_TEST",
                    semantic_updates={"course_scope_status": "COURSE_CONTENT"},
                )

        engine = WorkflowEngine(generator=CourseAnswerGenerator())
        idea = (
            "我想探究静电场中各种物体周围的电场线分布，以及它们放在一起时"
            "怎样改变彼此附近的场线形状和空间分布。"
        )

        result = engine.create_design(idea)

        self.assertEqual(result["stage_payload"]["brainstorm_phase"], "DEPTH_EXPANSION")
        self.assertEqual(result["stage_payload"]["exploration_scenes"], [])
        self.assertIn("experiment_outline_seed", result["stage_payload"])
        stored = engine.store.get(result["design_id"])
        self.assertEqual(stored.design_context["idea"]["core_phenomenon"], idea)

    def test_long_scene_response_is_committed_without_a_second_confirmation(self) -> None:
        """A contextual answer after A/B/C is a direction, not another scene request."""

        class BareContextualAnswerGenerator(RuleBasedStageGenerator):
            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                return resolved_intent(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    target=str(pending_action.get("subject") or "")
                    if pending_action
                    else None,
                    resolved_value=user_message,
                    confidence=0.98,
                    source="SEMANTIC_TEST",
                    semantic_updates={},
                )

        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design("我想做一个有关静电场的实验")
        self.assertEqual(len(first["stage_payload"]["exploration_scenes"]), 3)
        engine.generator = BareContextualAnswerGenerator()

        response = (
            "我对导体与介质在同样外加电场中的差异感兴趣，想比较它们周围"
            "电场线的分布和弯曲，并观察材料边界怎样改变局部场。"
        )
        result = engine.process_turn(first["design_id"], {"message": response})
        stored = engine.store.get(first["design_id"])

        self.assertEqual(result["stage_payload"].get("exploration_scenes"), [])
        self.assertTrue(result["stage_payload"].get("direction_locked"))
        self.assertNotIn("请确认沿用", result["assistant_message"])
        self.assertIn(
            response,
            {
                str(stored.design_context["idea"].get("core_phenomenon") or ""),
                str(stored.design_context["idea"].get("current_focus") or ""),
                str(design_state_snapshot(stored).get("research_object") or ""),
            },
        )
        self.assertEqual(current_pending_action(stored)["subject"], "research_question")

    def test_later_stage_partial_answers_accumulate_without_overwriting(self) -> None:
        class IncrementalStageGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={"pending_answer_status": "CLEAR"},
                )
                self.generation_count = 0

            def generate(self, session, user_message):
                self.generation_count += 1
                if self.generation_count == 1:
                    return StepOutput(
                        assistant_message="先保留主动改变量。",
                        stage_payload={
                            "independent_variable": "两个源之间的距离",
                            "stage_readiness": {
                                "ready_for_confirmation": False,
                                "remaining_gaps": ["controlled_conditions"],
                            },
                        },
                        student_task="还需要保持哪些条件不变？",
                    )
                return StepOutput(
                    assistant_message="控制条件也已补充。",
                    stage_payload={
                        "controlled_variables": ["电荷量", "观察方式"],
                        "stage_readiness": {
                            "ready_for_confirmation": True,
                            "remaining_gaps": [],
                        },
                    },
                    student_task=None,
                )

        session = DesignSession(
            design_id="design_accumulated_later_stage",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
            design_context={"idea": {"main_direction": "比较两个场源"}},
        )
        entry = guided_stage_entry_output(session)
        save_pending_action(session, session.current_stage, entry)
        session.stage_outputs[session.current_stage.value] = {
            "stage_payload": entry.stage_payload,
            "assistant_message": entry.assistant_message,
        }
        engine = WorkflowEngine(generator=IncrementalStageGenerator())
        engine.store.save(session)

        engine.process_turn(
            session.design_id,
            {"message": "主动改变两个源之间的距离"},
        )
        engine.process_turn(
            session.design_id,
            {"message": "保持电荷量和观察方式不变"},
        )

        stored = engine.store.get(session.design_id)
        draft = stored.design_context["guided_stage_drafts"][
            Stage.VARIABLES_AND_CONDITIONS.value
        ]
        self.assertEqual(draft["independent_variable"], "两个源之间的距离")
        self.assertEqual(draft["controlled_variables"], ["电荷量", "观察方式"])
        carried = build_carried_context(stored)
        self.assertIn("两个源之间的距离", carried["independent_variable"])
        self.assertIn("电荷量", carried["controlled_conditions"])

    def test_semantic_no_direction_replaces_phrase_list(self) -> None:
        paraphrases = (
            "没有思路",
            "脑子里还是一片空白，想先看看可能性",
            "我不知道该从哪里开始",
            "暂时想不到能做什么，先帮我打开思路",
        )
        for message in paraphrases:
            with self.subTest(message=message):
                generator = ScriptedSemanticGenerator(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={
                        "no_direction": True,
                        "selected_option_ids": [],
                        "facet_updates": [],
                        "comparison_updates": [],
                    },
                )
                engine = WorkflowEngine(generator=generator)

                result = engine.create_design(message)

                self.assertEqual(
                    result["stage_payload"]["input_category"],
                    "COURSE_CONTENT",
                )
                self.assertEqual(
                    result["stage_payload"]["brainstorm_phase"],
                    "BREADTH_EXPLORATION",
                )
                self.assertEqual(len(result["stage_payload"]["alternative_ideas"]), 3)
                self.assertIn("帮助你拓展思路", result["assistant_message"])
                self.assertNotIn("不属于ECE329", result["assistant_message"])
                self.assertNotIn("超出ECE329", result["assistant_message"])

    def test_semantic_option_ids_drive_single_and_combined_scene_selection(self) -> None:
        generator = ScriptedSemanticGenerator(UserIntent.ANSWER_CURRENT_QUESTION)
        engine = WorkflowEngine(generator=generator)
        first = engine.create_design("我想研究传输线中的反射和驻波")
        options = first["stage_payload"]["alternative_ideas"]
        selected_ids = [options[0]["option_id"], options[1]["option_id"]]
        generator.semantic_updates = {
            "no_direction": False,
            "selected_option_ids": selected_ids,
            "facet_updates": [],
            "comparison_updates": [],
        }

        result = engine.process_turn(
            first["design_id"],
            {"message": "我想把刚才提到的两个画面放在一起看"},
        )

        relations = result["stage_payload"]["selected_course_relations"]
        self.assertEqual(
            [item["option_id"] for item in relations],
            selected_ids,
        )
        self.assertTrue(result["stage_payload"]["combination_intent"])

    def test_more_examples_semantic_intent_bypasses_gap_check_without_losing_context(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            target="exploration_scenes",
        )
        engine = WorkflowEngine(generator=generator)
        first = engine.create_design("我想研究传输线中的反射和驻波")
        session = engine.store.get(first["design_id"])
        session.design_context["idea"].update(
            {
                "topic_anchor": "传输线中的反射和驻波",
                "current_focus": "比较负载变化与反射、驻波图样的关系",
                "course_scope_confirmed": True,
            }
        )
        initialize_idea_development(
            session,
            {
                "core_phenomenon": "负载变化时反射与驻波图样发生变化",
                "course_relationships": ["传输线、反射与驻波"],
            },
        )
        engine.store.save(session)
        generator.intent = UserIntent.REQUEST_MORE_EXAMPLES
        generator.semantic_updates = {
            "course_scope_status": "COURSE_CONTENT",
            "control_actions": ["REQUEST_REFERENCE"],
            "stage_one_scene_response": "REQUEST_NEW_BATCH",
            "scene_batch_authorized": True,
        }

        result = engine.process_turn(
            first["design_id"],
            {"message": "这些还没启发我，能不能再换一组图景"},
        )

        self.assertEqual(
            result["stage_payload"]["brainstorm_phase"],
            "BREADTH_EXPLORATION",
        )
        self.assertEqual(len(result["stage_payload"]["alternative_ideas"]), 3)
        stored = engine.get_design(first["design_id"])["design_context"]["idea"]
        self.assertEqual(stored["topic_anchor"], "传输线中的反射和驻波")
        self.assertIn("反射", stored["current_focus"])

    def test_scene_selection_cannot_be_replayed_as_another_breadth_batch(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design("我想探索静电场中的物体相互影响")
        self.assertEqual(len(first["stage_payload"]["exploration_scenes"]), 3)
        generator = ScriptedSemanticGenerator(
            UserIntent.REQUEST_MORE_EXAMPLES,
            target="exploration_scenes",
            semantic_updates={
                "course_scope_status": "COURSE_CONTENT",
                "control_actions": ["REQUEST_REFERENCE"],
                "stage_one_scene_response": "REQUEST_NEW_BATCH",
                # Deliberately omit scene_batch_authorized: this reproduces a
                # broad-parser false positive like trail31 and must fail safe.
            },
        )
        engine.generator = generator
        selection = "我选择两个场源靠近的图景，并想观察它们之间的场线重分布"

        result = engine.process_turn(
            first["design_id"],
            {"message": selection},
        )

        self.assertEqual(result["stage_payload"].get("exploration_scenes"), [])
        self.assertEqual(result["stage_payload"].get("alternative_ideas"), [])
        self.assertTrue(result["stage_payload"]["scene_replay_avoided"])
        self.assertIn("三幅图景不会再次展示", result["assistant_message"])
        self.assertEqual(
            result["stage_payload"]["clarification_choices"][0]["label"],
            "沿用这个研究重点",
        )
        stored = engine.store.get(first["design_id"])
        pending = current_pending_action(stored)
        self.assertEqual(pending.get("candidate_answer"), selection)
        self.assertTrue(pending.get("candidate_binding_authorized"))
        self.assertEqual(
            pending.get("candidate_purpose"),
            STAGE_ONE_DIRECTION_CANDIDATE,
        )
        before_confirmation = design_state_snapshot(stored)

        generator.intent = UserIntent.ACCEPT_PREVIOUS_PROPOSAL
        generator.semantic_updates = {}
        accepted = engine.process_turn(
            first["design_id"],
            {"message": "是"},
        )

        self.assertEqual(accepted["stage_payload"].get("exploration_scenes"), [])
        self.assertTrue(accepted["stage_payload"]["direction_locked"])
        self.assertIn("已经沿用刚才确定的研究方向", accepted["assistant_message"])
        self.assertNotIn("请确认沿用", accepted["assistant_message"])
        confirmed_session = engine.store.get(first["design_id"])
        next_pending = current_pending_action(confirmed_session)
        self.assertIsNotNone(next_pending)
        self.assertEqual(next_pending["subject"], "research_question")
        after_confirmation = design_state_snapshot(confirmed_session)
        # Confirming the direction is a dialogue-state transition. It must not
        # copy the complete scene-selection utterance into the facet that was
        # awaiting input when the response guard intervened.
        self.assertEqual(
            after_confirmation["learning_objective"],
            before_confirmation["learning_objective"],
        )
        self.assertNotEqual(after_confirmation["learning_objective"], selection)

        generator.intent = UserIntent.ADVANCE_STAGE
        generator.semantic_updates = {}
        continued = engine.process_turn(
            first["design_id"],
            {"message": "继续完善后面的内容"},
        )

        self.assertEqual(continued["stage_payload"].get("exploration_scenes"), [])
        self.assertNotIn("请确认沿用", continued["assistant_message"])
        self.assertNotIn("我已经保留你刚才", continued["assistant_message"])
        continued_pending = current_pending_action(engine.store.get(first["design_id"]))
        self.assertIsNotNone(continued_pending)
        self.assertEqual(continued_pending["subject"], "research_question")
        self.assertFalse(
            str(continued_pending.get("candidate_answer") or "").strip()
        )

    def test_authoritative_control_only_scene_confirmation_closes_candidate(self) -> None:
        """The Responses schema may encode a confirmation with no field acts."""

        candidate = "比较导体和介质在相同外加电场中的场线分布与弯曲"
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "direction_outline",
            "candidate_answer": candidate,
            "candidate_binding_authorized": True,
            "candidate_purpose": STAGE_ONE_DIRECTION_CANDIDATE,
            "allowed_intents": [
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.UNCLEAR.value,
            ],
        }

        accepted = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[],
                actions_authoritative=True,
            ),
            pending,
        )

        self.assertEqual(
            accepted["intent"], UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        )
        self.assertEqual(accepted["source"], "CONFIRMED_STAGE_ONE_DIRECTION")
        self.assertEqual(
            accepted["semantic_updates"]["stage_one_scene_response"],
            "SELECT_OR_DEVELOP",
        )
        self.assertEqual(
            accepted["semantic_updates"]["stage_one_direction_detail"],
            candidate,
        )

    def test_authoritative_advance_consumes_saved_scene_direction(self) -> None:
        """Continuing from a saved direction must not replay its confirmation."""

        candidate = "比较两个场源靠近时中间区域的电场线重分布"
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "direction_outline",
            "candidate_answer": candidate,
            "candidate_binding_authorized": True,
            "candidate_purpose": STAGE_ONE_DIRECTION_CANDIDATE,
            "allowed_intents": [UserIntent.UNCLEAR.value],
            "advance_on_accept": False,
        }

        accepted = validate_resolved_intent(
            resolved_intent(
                UserIntent.ADVANCE_STAGE,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[],
                actions_authoritative=True,
            ),
            pending,
        )

        self.assertEqual(
            accepted["intent"], UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        )
        self.assertEqual(accepted["source"], "CONFIRMED_STAGE_ONE_DIRECTION")
        self.assertFalse(accepted["advance_requested"])
        self.assertIn("ACCEPT", accepted["semantic_updates"]["control_actions"])
        self.assertNotIn("ADVANCE", accepted["semantic_updates"]["control_actions"])

        controlled = validate_resolved_intent(
            resolved_intent(
                UserIntent.UNCLEAR,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "CONTROL",
                        "target": "ADVANCE",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    }
                ],
                actions_authoritative=True,
            ),
            pending,
        )

        self.assertEqual(
            controlled["intent"], UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        )
        self.assertEqual(
            controlled["dialogue_acts"][0]["target"],
            "ACCEPT",
        )
        self.assertFalse(controlled["advance_requested"])

    def test_authoritative_advance_consumes_saved_emvr_field_then_asks_next(self) -> None:
        """EMVR continuation commits the exact candidate before readiness runs."""

        engine = WorkflowEngine(
            generator=ScriptedSemanticGenerator(UserIntent.ADVANCE_STAGE)
        )
        created = engine.create_design(
            "进入EMVR模式",
            InteractionState.EMVR_DIRECT,
        )
        design_id = created["design_id"]
        session = engine.store.get(design_id)
        candidate = (
            "学生在VR中拖动两个带电物体改变间距，并观察两者之间电场线的弯曲与重排"
        )
        pending = record_pending_clarification(
            session,
            candidate,
            allow_exact_field_binding=True,
        )
        self.assertIsNotNone(pending)
        engine.store.save(session)

        result = engine.process_turn(design_id, {"message": "继续进行后面的设计"})
        stored = engine.store.get(design_id)
        requirements = merge_emvr_structured_requirements(
            stored.design_context["emvr_design"]
        )

        self.assertEqual(requirements["experiment_brief"], candidate)
        self.assertNotIn("上一轮提供的设计描述已经保留", result["assistant_message"])
        next_pending = current_pending_action(stored)
        self.assertIsNotNone(next_pending)
        self.assertEqual(next_pending["subject"], "research_object")
        self.assertTrue(str(result.get("student_task") or "").strip())

    def test_legacy_guarded_scene_candidate_is_relinked_after_reload(self) -> None:
        """Persisted pre-contract Stage-1 sessions recover their direction link."""

        session = DesignSession(
            design_id="legacy_guarded_direction",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        state = session.model_context.setdefault("dialogue_state", {})
        state["pending_action"] = {
            "action_id": "legacy_action",
            "type": "ANSWER_IDEA_FACET",
            "subject": "direction_outline",
            "candidate_answer": "比较不同介质边界附近的电场线变化",
            "candidate_binding_authorized": False,
            "allowed_intents": [UserIntent.UNCLEAR.value],
            "status": "PENDING",
        }
        session.history.append(
            {
                "handled_stage": Stage.IDEA_BRAINSTORMING.value,
                "output": {
                    "assistant_message": "",
                    "stage_payload": {"scene_replay_avoided": True},
                },
            }
        )

        migrated = hydrate_pending_action_from_history(session)

        self.assertIsNotNone(migrated)
        self.assertTrue(migrated["candidate_binding_authorized"])
        self.assertEqual(
            migrated["candidate_purpose"], STAGE_ONE_DIRECTION_CANDIDATE
        )

    def test_locked_legacy_direction_discards_stale_confirmation_pending(self) -> None:
        """A confirmed direction must not keep an obsolete direction prompt alive."""

        session = DesignSession(
            design_id="legacy_locked_direction",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "direction_locked": True,
                    "direction_summary": "比较不同边界附近的电场线变化",
                }
            },
        )
        state = session.model_context.setdefault("dialogue_state", {})
        state["pending_action"] = {
            "action_id": "obsolete_direction_action",
            "type": "ANSWER_IDEA_FACET",
            "subject": "direction_outline",
            "candidate_answer": "此前已确认的方向",
            "candidate_binding_authorized": True,
            "candidate_purpose": STAGE_ONE_DIRECTION_CANDIDATE,
            "status": "PENDING",
        }

        migrated = hydrate_pending_action_from_history(session)

        self.assertIsNone(migrated)
        self.assertIsNone(current_pending_action(session))

    def test_low_confidence_scene_confirmation_cannot_lock_direction(self) -> None:
        candidate = "比较导体和介质在相同外加电场中的场线分布"
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "direction_outline",
            "candidate_answer": candidate,
            "candidate_binding_authorized": True,
            "candidate_purpose": STAGE_ONE_DIRECTION_CANDIDATE,
            "allowed_intents": [
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.UNCLEAR.value,
            ],
        }

        unresolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                confidence=0.31,
                source="SEMANTIC_MODEL",
                dialogue_acts=[],
                actions_authoritative=True,
            ),
            pending,
        )

        self.assertEqual(unresolved["intent"], UserIntent.UNCLEAR.value)
        self.assertNotEqual(
            unresolved["semantic_updates"].get("stage_one_scene_response"),
            "SELECT_OR_DEVELOP",
        )

    def test_candidate_acceptance_and_parallel_edit_are_both_committed(self) -> None:
        """A control act may confirm one exact field beside another field edit."""

        session = DesignSession(
            design_id="emvr_confirm_and_edit",
            interaction_state=InteractionState.EMVR_DIRECT,
            design_context={"emvr_design": {}},
        )
        candidate = "学生在VR中拖动两个带电物体改变距离，并观察电场线变化"
        pending = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": "experiment_brief",
            "answer_fields": ["experiment_brief"],
            "candidate_answer": candidate,
            "candidate_binding_authorized": True,
            "allowed_intents": [
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.UNCLEAR,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "CONTROL",
                        "target": "ACCEPT",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_EMVR_FIELD",
                        "target": "research_object",
                        "operation": "REPLACE",
                        "content": "两个带电物体",
                        "confidence": 0.99,
                    },
                ],
                actions_authoritative=True,
            ),
            pending,
        )

        projected_fields = {
            str(item.get("field_id") or "")
            for item in resolved["semantic_updates"]["emvr_design_update"][
                "field_updates"
            ]
            if isinstance(item, dict)
        }
        self.assertEqual(
            projected_fields,
            {"experiment_brief", "research_object"},
        )
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"],
            "CLEAR",
        )
        apply_resolved_intent(
            session,
            resolved,
            pending,
            user_message="沿用上一条，同时把研究对象写成两个带电物体",
        )
        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            resolved["semantic_updates"]["emvr_design_update"],
        )
        requirements = merge_emvr_structured_requirements(
            session.design_context["emvr_design"]
        )
        self.assertEqual(requirements["experiment_brief"], candidate)
        self.assertEqual(requirements["research_object"], "两个带电物体")

    def test_idea_gap_pending_action_names_the_exact_active_facet(self) -> None:
        session = idea_facet_session("design_facet_pending")

        pending = current_pending_action(session)

        assert pending is not None
        self.assertEqual(pending["type"], "ANSWER_IDEA_FACET")
        self.assertEqual(pending["subject"], "research_question")
        self.assertEqual(pending["proposal"]["title"], "研究问题")

    def test_stage_one_pending_is_rebuilt_from_canonical_facet_state(self) -> None:
        session = idea_facet_session("design_canonical_facet_pending")

        pending = save_pending_action(
            session,
            Stage.IDEA_BRAINSTORMING,
            StepOutput(
                assistant_message="请继续说明。",
                stage_payload={
                    "pending_action": {
                        "type": "ANSWER_IDEA_FACET",
                        "subject": None,
                        "proposal": {"facet_id": None, "title": None},
                        "question": "请继续说明。",
                    }
                },
                student_task="请继续说明。",
            ),
        )

        assert pending is not None
        self.assertEqual(pending["subject"], "research_question")
        self.assertEqual(pending["proposal"]["facet_id"], "research_question")
        self.assertEqual(pending["proposal"]["title"], "研究问题")
        self.assertNotIn("None", clarification_output(pending).assistant_message)

    def test_course_reference_candidate_survives_pending_normalization(self) -> None:
        session = idea_facet_session("design_reference_candidate")
        output = build_facet_reference_output(session)

        pending = save_pending_action(session, Stage.IDEA_BRAINSTORMING, output)

        assert pending is not None
        self.assertEqual(pending["subject"], "research_question")
        self.assertTrue(str(pending.get("candidate_answer") or "").strip())
        accepted = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                target="research_question",
                confidence=0.98,
            ),
            pending,
        )
        self.assertEqual(accepted["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value)
        self.assertEqual(accepted["source"], "CONFIRMED_PENDING_ANSWER")
        self.assertEqual(
            accepted["semantic_updates"]["facet_updates"],
            [{"facet_id": "research_question", "status": "CLEAR"}],
        )

    def test_accepting_course_reference_moves_past_research_question(self) -> None:
        session = idea_facet_session("design_accept_reference")
        save_pending_action(
            session,
            Stage.IDEA_BRAINSTORMING,
            build_facet_reference_output(session),
        )
        engine = WorkflowEngine(
            generator=ScriptedSemanticGenerator(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                target="research_question",
            )
        )
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "同意，继续"},
        )

        status = result["stage_payload"]["idea_development_status"]
        self.assertEqual(
            status["facets_by_id"]["research_question"]["status"],
            "CLEAR",
        )
        self.assertEqual(status["active_facet_id"], "learning_objective")
        self.assertNotIn("研究问题还需要", result["assistant_message"])

    def test_legacy_generic_stage_one_pending_is_migrated_before_next_turn(self) -> None:
        session = idea_facet_session("design_legacy_generic_pending")
        session.model_context["dialogue_state"]["pending_action"].update(
            {
                "type": "ANSWER_CURRENT_QUESTION",
                "subject": "idea_brainstorming",
                "proposal": {},
            }
        )

        pending = hydrate_pending_action_from_history(session)

        assert pending is not None
        self.assertEqual(pending["type"], "ANSWER_IDEA_FACET")
        self.assertEqual(pending["subject"], "research_question")
        self.assertEqual(
            session.model_context["dialogue_state"]["pending_action"]["subject"],
            "research_question",
        )

    def test_valid_research_answer_updates_facet_and_comparison_in_one_turn(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "research_question", "status": "CLEAR"}
                ],
                "comparison_updates": [
                    {
                        "comparison_id": "polarity_cases",
                        "action": "ACCEPT",
                        "cases": ["同种电荷", "异种电荷"],
                    }
                ],
            },
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_trail8_research_answer")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {
                "message": (
                    "在两个电荷源带同种或异种电荷的条件下，逐渐缩短源之间的距离，"
                    "观察场线形状、幅度或空间分布的变化"
                )
            },
        )

        status = result["stage_payload"]["idea_development_status"]
        self.assertEqual(
            status["facets_by_id"]["research_question"]["status"],
            "CLEAR",
        )
        self.assertEqual(status["active_facet_id"], "learning_objective")
        self.assertIn("研究问题已经很具体", result["assistant_message"])
        self.assertNotIn("研究问题还需要同时出现", result["assistant_message"])
        comparison = engine.get_design(session.design_id)["design_context"]["idea"][
            "standard_comparisons"
        ][0]
        self.assertEqual(comparison["adoption_status"], "ACCEPTED")

    def test_multi_act_turn_commits_answer_observation_and_course_question(self) -> None:
        research = "比较同种与异种电荷靠近时，中间区域的场线如何变化"
        observation = "观察空白区和弯曲连接随距离的变化"
        question = "为什么异种电荷的场线会弯曲相连？"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": research,
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "observations",
                    "operation": "MERGE",
                    "content": [observation],
                    "confidence": 0.98,
                },
                {
                    "type": "ASK_COURSE_QUESTION",
                    "target": "course_explanation",
                    "operation": "MERGE",
                    "content": question,
                    "confidence": 0.98,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_multi_act_answer_question")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"{research}，另外{observation}。{question}"},
        )

        self.assertEqual(
            result["stage_payload"]["design_state"]["research_question"],
            research,
        )
        self.assertEqual(
            result["stage_payload"]["stage_design_state"]["observations"],
            observation,
        )
        self.assertEqual(
            result["stage_payload"]["idea_development_status"]["active_facet_id"],
            "learning_objective",
        )
        self.assertIn("研究问题", result["assistant_message"])
        self.assertIn("观察内容", result["assistant_message"])
        stored = engine.get_design(session.design_id, include_history=True)
        resolved = stored["history"][-1]["resolved_intent"]
        self.assertEqual(resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value)

    def test_multi_act_edit_other_field_keeps_pending_research_question(self) -> None:
        objective = "能够解释电荷极性与距离如何共同影响场线分布"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "learning_objective",
                    "operation": "REPLACE",
                    "content": objective,
                    "confidence": 0.99,
                }
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_multi_act_nonpending_edit")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"先把学习目标改成：{objective}，研究问题我稍后再补。"},
        )

        status = result["stage_payload"]["idea_development_status"]
        self.assertEqual(status["facets_by_id"]["learning_objective"]["status"], "CLEAR")
        self.assertEqual(status["active_facet_id"], "research_question")
        self.assertEqual(result["stage_payload"]["design_state"]["learning_objective"], objective)
        self.assertIn("学习目标", result["assistant_message"])

    def test_partial_multi_act_commits_valid_part_and_clarifies_only_remainder(self) -> None:
        hypothesis = "距离越近，场线弯曲和重排越明显"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "hypothesis",
                    "operation": "REPLACE",
                    "content": hypothesis,
                    "confidence": 0.98,
                },
                {
                    "type": "UNRESOLVED",
                    "target": "",
                    "operation": "MERGE",
                    "content": "后半句关于另一个比较我还没说清",
                    "confidence": 0.91,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_partial_multi_act")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"我的预测是{hypothesis}，后半句关于另一个比较我还没说清。"},
        )

        self.assertEqual(result["stage_payload"]["design_state"]["hypothesis"], hypothesis)
        self.assertIn("假设", result["assistant_message"])
        self.assertIn("还有一句我没完全理解", result["assistant_message"])
        self.assertNotIn("我还不能确定你希望继续", result["assistant_message"])

    def test_multi_act_later_stage_updates_fields_independently(self) -> None:
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "independent_variable",
                    "operation": "REPLACE",
                    "content": "两个电荷之间的距离",
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "observations",
                    "operation": "MERGE",
                    "content": ["中间区域场线形状", "零场区域位置"],
                    "confidence": 0.98,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "controlled_conditions",
                    "operation": "REPLACE",
                    "content": ["电荷量大小", "观察平面"],
                    "confidence": 0.98,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = variable_stage_session("design_multi_act_variables")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "距离作为自变量，观察场线和零场位置，并保持电荷量与观察平面不变。"},
        )

        stage_state = result["stage_payload"]["stage_design_state"]
        self.assertEqual(stage_state["independent_variable"], "两个电荷之间的距离")
        self.assertIn("中间区域场线形状", stage_state["observations"])
        self.assertIn("零场区域位置", stage_state["observations"])
        self.assertIn("电荷量大小", stage_state["controlled_conditions"])
        self.assertNotEqual(
            stage_state["independent_variable"],
            stage_state["observations"],
        )

    def test_stage_answer_can_target_a_field_inside_the_pending_stage(self) -> None:
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "ANSWER_PENDING_QUESTION",
                    "target": "independent_variable",
                    "operation": "REPLACE",
                    "content": "两个电荷之间的距离",
                    "confidence": 0.99,
                }
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = variable_stage_session("design_stage_field_answer")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "我会主动改变两个电荷之间的距离。"},
        )

        self.assertEqual(
            result["stage_payload"]["stage_design_state"]["independent_variable"],
            "两个电荷之间的距离",
        )
        resolved = engine.store.get(session.design_id).model_context[
            "dialogue_state"
        ]["resolved_intent"]
        self.assertEqual(resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value)
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"],
            "CLEAR",
        )

    def test_multi_act_updates_are_shared_by_emvr_mode(self) -> None:
        research = "距离减小时，导体与介质附近的电场线分布如何变化"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": research,
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "unity_objects",
                    "operation": "MERGE",
                    "content": ["点电荷源", "可切换材料的测试物体"],
                    "confidence": 0.98,
                },
                {
                    "type": "ASK_COURSE_QUESTION",
                    "target": "course_explanation",
                    "operation": "MERGE",
                    "content": "介质为什么会改变附近的场线？",
                    "confidence": 0.97,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = variable_stage_session("design_emvr_multi_act")
        session.interaction_state = InteractionState.EMVR_DIRECT
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "请改研究问题并加入两个Unity对象，另外解释介质为何改变场线。"},
        )

        self.assertEqual(result["stage_payload"]["design_state"]["research_question"], research)
        self.assertIn(
            "点电荷源",
            result["stage_payload"]["stage_design_state"]["unity_objects"],
        )
        self.assertIn("研究问题", result["assistant_message"])
        self.assertIn("VR实验对象", result["assistant_message"])

    def test_mode_specific_stage_fields_cannot_leak_across_workflows(self) -> None:
        guided_engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "unity_objects",
                        "operation": "MERGE",
                        "content": ["不应进入引导模式的Unity对象"],
                        "confidence": 0.99,
                    }
                ]
            )
        )
        guided = variable_stage_session("design_guided_reject_emvr_field")
        guided_engine.store.save(guided)
        guided_engine.process_turn(
            guided.design_id,
            {"message": "增加一个Unity对象，但不要切换模式。"},
        )
        self.assertEqual(
            stage_design_state_snapshot(
                guided_engine.store.get(guided.design_id)
            )["unity_objects"],
            "",
        )

        emvr_engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "student_summary",
                        "operation": "REPLACE",
                        "content": "不应进入EMVR状态的学生总结字段内容",
                        "confidence": 0.99,
                    }
                ]
            )
        )
        emvr = variable_stage_session("design_emvr_reject_guided_summary")
        emvr.interaction_state = InteractionState.EMVR_DIRECT
        emvr_engine.store.save(emvr)
        emvr_engine.process_turn(
            emvr.design_id,
            {"message": "写入学生总结字段。"},
        )
        self.assertEqual(
            stage_design_state_snapshot(
                emvr_engine.store.get(emvr.design_id)
            )["student_summary"],
            "",
        )

    def test_field_update_and_summary_request_both_execute(self) -> None:
        objective = "能够解释距离与电荷极性如何共同改变场线分布"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "learning_objective",
                        "operation": "REPLACE",
                        "content": objective,
                        "confidence": 0.99,
                    },
                    {
                        "type": "REQUEST_SUMMARY",
                        "target": "current_design",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.98,
                    },
                ]
            )
        )
        session = idea_facet_session("design_update_and_summary")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "把学习目标改成这句话，然后把当前设计整体列出来。"},
        )

        self.assertTrue(result["stage_payload"]["read_only_design_summary"])
        self.assertEqual(
            result["stage_payload"]["design_state"]["learning_objective"],
            objective,
        )
        self.assertIn(objective, result["assistant_message"])

    def test_stage_field_update_and_advance_both_execute(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "observations",
                        "operation": "MERGE",
                        "content": "同时记录零场位置",
                        "confidence": 0.99,
                    },
                    {
                        "type": "CONTROL",
                        "target": "ADVANCE",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = variable_stage_session("design_update_and_advance")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "再记录零场位置，然后继续整理后面的流程。"},
        )

        self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_PROCEDURE.value)
        self.assertIn(
            "零场位置",
            result["stage_payload"]["stage_design_state"]["observations"],
        )

    def test_structured_stage_answer_can_advance_without_old_draft_payload(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "ANSWER_PENDING_QUESTION",
                        "target": "independent_variable",
                        "operation": "REPLACE",
                        "content": "两个源之间的距离",
                        "confidence": 0.99,
                    },
                    {
                        "type": "CONTROL",
                        "target": "ADVANCE",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = variable_stage_session("design_structured_answer_advance")
        session.stage_outputs = {}
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "我会改变两个源的距离，然后继续整理流程。"},
        )

        self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_PROCEDURE.value)
        self.assertEqual(
            result["stage_payload"]["stage_design_state"]["independent_variable"],
            "两个源之间的距离",
        )

    def test_emvr_structured_answer_and_advance_are_processed_together(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "ANSWER_PENDING_QUESTION",
                        "target": "independent_variable",
                        "operation": "REPLACE",
                        "content": "两个带电物体之间的距离",
                        "confidence": 0.99,
                    },
                    {
                        "type": "CONTROL",
                        "target": "ADVANCE",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = variable_stage_session("design_emvr_answer_advance")
        session.interaction_state = InteractionState.EMVR_DIRECT
        session.design_context.setdefault("stage_design_state", {}).update(
            {
                "lab_title": "双电荷实验",
                "lab_id": "ece329_charge_field",
                "desktop_interaction_plan": "鼠标拖动带电体，VR映射为手柄抓取",
                "room_spatial_requirements": "对象在前方，面板在两侧并保留操作空间",
                "hidden_object_lifecycle": "无",
                "parameter_specifications": "距离0.2 m至2.0 m，步长0.1 m",
            }
        )
        session.stage_outputs[Stage.VARIABLES_AND_CONDITIONS.value][
            "stage_payload"
        ]["awaiting_user_design_input"] = True
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "让距离连续减小，并继续整理VR实验流程。"},
        )

        self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_PROCEDURE.value)
        self.assertEqual(
            result["stage_payload"]["stage_design_state"]["independent_variable"],
            "两个带电物体之间的距离",
        )

    def test_stage_field_update_and_reference_request_both_execute(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "observations",
                        "operation": "MERGE",
                        "content": "记录中间区域场线",
                        "confidence": 0.99,
                    },
                    {
                        "type": "REQUEST_REFERENCE",
                        "target": "current_stage",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.98,
                    },
                ]
            )
        )
        session = variable_stage_session("design_update_and_reference")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "增加中间区域场线，并给我一套课程内变量参考。"},
        )

        self.assertTrue(result["stage_payload"]["reference_only"])
        self.assertIn(
            "中间区域场线",
            result["stage_payload"]["stage_design_state"]["observations"],
        )

    def test_accept_control_is_not_lost_beside_a_field_update(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "CONTROL",
                        "target": "ACCEPT",
                        "operation": "EXECUTE",
                        "content": None,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "observations",
                        "operation": "MERGE",
                        "content": "增加零场位置",
                        "confidence": 0.98,
                    },
                ]
            )
        )
        session = variable_stage_session("design_accept_and_update")
        pending = current_pending_action(session)
        assert pending is not None
        subject = pending["subject"]
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "保留这套变量安排，再增加零场位置。"},
        )

        stored = engine.store.get(session.design_id)
        self.assertIn(subject, stored.design_context["resolved_decisions"])
        self.assertIn(
            "零场位置",
            result["stage_payload"]["stage_design_state"]["observations"],
        )

    def test_invalid_subaction_does_not_hide_valid_commit(self) -> None:
        hypothesis = "距离越近，场线重排越明显"
        bad_fragment = "把这句放到一个不存在的设计位置"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "hypothesis",
                        "operation": "REPLACE",
                        "content": hypothesis,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "unknown_field",
                        "operation": "MERGE",
                        "content": bad_fragment,
                        "confidence": 0.96,
                    },
                ]
            )
        )
        session = idea_facet_session("design_partial_invalid_action")
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": "同时修改两项。"})

        self.assertEqual(result["stage_payload"]["design_state"]["hypothesis"], hypothesis)
        self.assertIn(bad_fragment, result["assistant_message"])
        self.assertIn("还有一句我没完全理解", result["assistant_message"])

    def test_course_question_is_not_saved_as_pending_design_answer(self) -> None:
        question = "为什么异种电荷的场线会从正电荷连向负电荷？"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "ASK_COURSE_QUESTION",
                        "target": "electrostatics_explanation",
                        "operation": "MERGE",
                        "content": question,
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = idea_facet_session("design_question_not_fact")
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": question})

        self.assertEqual(result["stage_payload"]["design_state"]["research_question"], "")
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["subject"], "research_question")

    def test_correction_and_rewrite_are_separate_actions(self) -> None:
        corrected = "比较两种极性配置下，距离变化如何影响中间区域场线"
        feedback = "你刚才把观察现象写成了新的实验方向"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "CORRECT_ASSISTANT",
                        "target": "previous_interpretation",
                        "operation": "MERGE",
                        "content": feedback,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "research_question",
                        "operation": "REPLACE",
                        "content": corrected,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = idea_facet_session("design_feedback_and_rewrite")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"{feedback}，请改成：{corrected}"},
        )

        research = result["stage_payload"]["design_state"]["research_question"]
        self.assertEqual(research, corrected)
        self.assertNotIn(feedback, research)
        stored = engine.store.get(session.design_id)
        guided_inputs = stored.design_context["guided_stage_inputs"][
            Stage.IDEA_BRAINSTORMING.value
        ]
        self.assertEqual(guided_inputs[-1]["content"], corrected)
        self.assertNotIn(feedback, str(guided_inputs[-1]["content"]))

    def test_complex_correction_commits_nested_comparison_replacement(self) -> None:
        old_cases = ["无损线路", "有损线路"]
        new_cases = ["直线路径", "圆弧路径", "闭合路径"]
        message = (
            "有一处需要调整：基础比较目前写的是“无损线路、有损线路”，"
            "但这与研究问题（比较不同路径形状下场量分布的差异）不直接对应。"
            "建议将基础比较改为“直线路径、圆弧路径、闭合路径”，这样更贴近研究方向。"
        )
        correction_act = {
            "type": "CORRECT_ASSISTANT",
            "target": "previous_design_draft",
            "operation": "MERGE",
            "content": {
                "error_type": "DESIGN_MISMATCH",
                "explanation": "原基础比较与研究问题不一致",
                "affected_fields": ["baseline_comparisons"],
                "comparison_updates": [
                    {
                        "comparison_id": "path_cases",
                        "action": "REPLACE",
                        "cases": new_cases,
                        "replace_all": True,
                        "semantic_key": "probe_path_geometry_cases",
                    }
                ],
            },
            "confidence": 0.99,
        }
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator([correction_act])
        )
        session = idea_facet_session("design_complex_comparison_correction")
        set_baseline_comparisons(
            session,
            [
                {
                    "comparison_id": "path_cases",
                    "title": "基础比较",
                    "recommended_cases": old_cases,
                    "cases": old_cases,
                    "adoption_status": "ACCEPTED",
                }
            ],
        )
        old_pending = save_pending_action(
            session,
            Stage.IDEA_BRAINSTORMING,
            StepOutput(
                assistant_message="请整体看一遍当前想法。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "subject": Stage.IDEA_BRAINSTORMING.value,
                        "proposal": {"baseline_comparisons": old_cases},
                        "allowed_intents": [
                            "ACCEPT_PREVIOUS_PROPOSAL",
                            "MODIFY_PREVIOUS_PROPOSAL",
                            "UNCLEAR",
                        ],
                    }
                },
                student_task="有需要调整的地方可以直接说明。",
            ),
        )
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": message})

        comparisons = result["stage_payload"]["design_state"][
            "baseline_comparisons"
        ]
        self.assertEqual(comparisons[0]["cases"], new_cases)
        self.assertNotIn("无损线路", comparisons[0]["cases"])
        self.assertNotIn("请告诉我具体要改哪一项", result["assistant_message"])
        self.assertIn("基础比较", result["assistant_message"])
        stored = engine.store.get(session.design_id)
        pending = current_pending_action(stored)
        if pending is not None:
            self.assertNotEqual(
                pending.get("action_id"),
                old_pending.get("action_id"),
            )

    def test_valid_revision_closes_every_confirmation_type_in_both_modes(self) -> None:
        for pending_type in ("CONFIRM_OR_MODIFY", "CONFIRM_STAGE_OR_MODIFY"):
            for mode in (
                InteractionState.GUIDED_DESIGN,
                InteractionState.EMVR_DIRECT,
            ):
                with self.subTest(pending_type=pending_type, mode=mode.value):
                    session = DesignSession(
                        design_id=f"confirm_{pending_type}_{mode.value}",
                        interaction_state=mode,
                        current_stage_index=list(Stage).index(
                            Stage.VARIABLES_AND_CONDITIONS
                        ),
                    )
                    set_baseline_comparisons(
                        session,
                        [
                            {
                                "comparison_id": "path_cases",
                                "recommended_cases": ["无损线路", "有损线路"],
                                "cases": ["无损线路", "有损线路"],
                                "adoption_status": "ACCEPTED",
                            }
                        ],
                    )
                    pending = save_pending_action(
                        session,
                        Stage.VARIABLES_AND_CONDITIONS,
                        StepOutput(
                            assistant_message="请检查当前草稿。",
                            stage_payload={
                                "pending_action": {
                                    "type": pending_type,
                                    "subject": Stage.VARIABLES_AND_CONDITIONS.value,
                                    "proposal": {
                                        "baseline_comparisons": [
                                            "无损线路",
                                            "有损线路",
                                        ]
                                    },
                                }
                            },
                            student_task="需要修改时直接说明。",
                        ),
                    )
                    message = "改为直线路径、圆弧路径和闭合路径。"
                    raw = resolved_intent(
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                        confidence=0.99,
                        source="SEMANTIC_MODEL",
                        dialogue_acts=[
                            {
                                "type": "CORRECT_ASSISTANT",
                                "target": "previous_design_draft",
                                "operation": "MERGE",
                                "content": {
                                    "error_type": "DESIGN_MISMATCH",
                                    "affected_fields": ["baseline_comparisons"],
                                    "comparison_updates": [
                                        {
                                            "comparison_id": "path_cases",
                                            "action": "MODIFY",
                                            "cases": [
                                                "直线路径",
                                                "圆弧路径",
                                                "闭合路径",
                                            ],
                                            "replace_all": True,
                                        }
                                    ],
                                },
                                "confidence": 0.99,
                            }
                        ],
                        actions_authoritative=True,
                    )
                    resolved = validate_resolved_intent(raw, pending)

                    apply_resolved_intent(session, resolved, pending, message)

                    self.assertIsNone(current_pending_action(session))
                    self.assertEqual(
                        design_state_snapshot(session)["baseline_comparisons"][0][
                            "cases"
                        ],
                        ["直线路径", "圆弧路径", "闭合路径"],
                    )

    def test_emvr_stage_context_excludes_feedback_from_a_mixed_revision(self) -> None:
        feedback = "你刚才把新增交互误解成了新的实验方向"
        interaction = "增加可移动探测器读取中间区域的理论场强"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "CORRECT_ASSISTANT",
                        "target": "previous_interpretation",
                        "operation": "MERGE",
                        "content": feedback,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "interactions",
                        "operation": "MERGE",
                        "content": interaction,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = idea_facet_session("design_emvr_clean_mixed_context")
        session.interaction_state = InteractionState.EMVR_DIRECT
        session.design_context["emvr_design"] = {
            "brief": "比较两个带电物体靠近时的场线变化",
            "current_brief": "比较两个带电物体靠近时的场线变化",
            "brief_revisions": [],
        }
        engine.store.save(session)

        engine.process_turn(
            session.design_id,
            {"message": f"{feedback}；{interaction}。"},
        )

        stored = engine.store.get(session.design_id)
        emvr_design = stored.design_context["emvr_design"]
        self.assertEqual(
            emvr_design["current_brief"],
            "比较两个带电物体靠近时的场线变化",
        )
        self.assertNotIn(feedback, emvr_design["current_brief"])
        stage_entry = emvr_design["stage_inputs"][Stage.IDEA_BRAINSTORMING.value][-1]
        self.assertEqual(stage_entry["content"], interaction)

    def test_new_topic_act_uses_only_its_structured_content(self) -> None:
        new_topic = "研究传输线负载匹配与反射系数的关系"
        new_question = "改变负载阻抗时，反射系数的幅值和相位怎样变化？"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "NEW_TOPIC",
                        "target": "experiment_direction",
                        "operation": "EXECUTE",
                        "content": new_topic,
                        "confidence": 0.99,
                    },
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "research_question",
                        "operation": "REPLACE",
                        "content": new_question,
                        "confidence": 0.99,
                    },
                ]
            )
        )
        session = variable_stage_session("design_structured_new_topic")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"换成{new_topic}，并把研究问题写成：{new_question}"},
        )

        stored = engine.store.get(session.design_id)
        self.assertEqual(stored.design_context["idea"]["original"], new_topic)
        self.assertEqual(
            result["stage_payload"]["design_state"]["research_question"],
            new_question,
        )
        self.assertNotIn("并把研究问题", stored.design_context["idea"]["original"])

    def test_emvr_topic_recovery_keeps_control_content_and_projections_separate(self) -> None:
        first_brief = (
            "学生用手柄拖动两个带电物体，改变距离和相对方向，观察电场线的合并、"
            "扭曲与重排，从而理解库仑定律和叠加原理。"
        )
        second_brief = (
            "学生在VR中拖动两个点电荷改变间距，比较同种与异种电荷配置下中间区域"
            "电场线的弯曲和连接变化。"
        )
        revised_brief = (
            "学生在VR中拖动两个点电荷由远到近，比较同种与异种电荷配置下中间区域"
            "电场线的弯曲、连接与重排。"
        )

        def parser_failure(session, message, pending, carried):
            return degraded_context_intent(
                session,
                message,
                pending,
                carried,
                source="SEMANTIC_SERVICE_FALLBACK",
            )

        generator = SequencedSemanticGenerator(
            [
                resolved_intent(
                    UserIntent.SET_INTERACTION_STATE,
                    target="interaction_state",
                    resolved_value=InteractionState.EMVR_DIRECT.value,
                    confidence=1.0,
                    source="SEMANTIC_TEST",
                    semantic_updates={
                        "interaction_state_request": InteractionState.EMVR_DIRECT.value
                    },
                ),
                parser_failure,
                resolved_intent(
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                    confidence=0.99,
                    source="SEMANTIC_TEST",
                    dialogue_acts=[],
                    actions_authoritative=True,
                ),
                resolved_intent(
                    UserIntent.UNCLEAR,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    semantic_updates={"topic_change_explicit": True},
                    dialogue_acts=[
                        {
                            "type": "REQUEST_NEW_TOPIC",
                            "target": "",
                            "operation": "EXECUTE",
                            "content": None,
                            "confidence": 0.99,
                        }
                    ],
                    actions_authoritative=True,
                ),
                resolved_intent(
                    UserIntent.UNCLEAR,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    semantic_updates={"topic_change_explicit": True},
                    dialogue_acts=[
                        {
                            "type": "NEW_TOPIC_CONTENT",
                            "target": "experiment_brief",
                            "operation": "EXECUTE",
                            "content": second_brief,
                            "confidence": 0.99,
                        },
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "research_object",
                            "operation": "REPLACE",
                            "content": "两个点电荷",
                            "confidence": 0.99,
                        },
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "required_behaviors",
                            "operation": "REPLACE",
                            "content": ["用手柄拖动两个点电荷"],
                            "confidence": 0.99,
                        },
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "changed_quantities",
                            "operation": "REPLACE",
                            "content": ["两个点电荷的间距", "电荷极性配置"],
                            "confidence": 0.99,
                        },
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "observed_quantities",
                            "operation": "REPLACE",
                            "content": ["中间区域电场线的弯曲和连接变化"],
                            "confidence": 0.99,
                        },
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "learning_objectives",
                            "operation": "REPLACE",
                            "content": ["解释库仑定律与场叠加如何共同决定空间场线"],
                            "confidence": 0.99,
                        },
                    ],
                    actions_authoritative=True,
                ),
                resolved_intent(
                    UserIntent.UNCLEAR,
                    confidence=0.99,
                    source="SEMANTIC_MODEL",
                    dialogue_acts=[
                        {
                            "type": "MODIFY_EMVR_FIELD",
                            "target": "experiment_brief",
                            "operation": "REPLACE",
                            "content": revised_brief,
                            "confidence": 0.99,
                        }
                    ],
                    actions_authoritative=True,
                ),
                resolved_intent(
                    UserIntent.ADVANCE_STAGE,
                    advance_requested=True,
                    confidence=0.99,
                    source="SEMANTIC_TEST",
                ),
            ]
        )
        engine = WorkflowEngine(generator=generator)
        created = engine.create_design(
            "进入EMVR模式",
            InteractionState.EMVR_DIRECT,
        )
        design_id = created["design_id"]
        initial = engine.store.get(design_id)
        self.assertEqual(initial.design_context["idea"]["original"], "")
        self.assertEqual(
            current_pending_action(initial)["answer_fields"],
            ["experiment_brief"],
        )

        failed = engine.process_turn(design_id, {"message": first_brief})
        pending = current_pending_action(engine.store.get(design_id))
        self.assertTrue(failed["stage_payload"]["clarification_required"])
        self.assertEqual(pending["candidate_answer"], first_brief)
        self.assertTrue(pending["candidate_binding_authorized"])
        self.assertFalse(
            engine.store.get(design_id).design_context["emvr_design"].get(
                "experiment_brief"
            )
        )

        recovered_output = engine.process_turn(
            design_id,
            {"message": "保留这部分并继续"},
        )
        recovered = engine.store.get(design_id)
        self.assertEqual(
            recovered.design_context["emvr_design"]["experiment_brief"],
            first_brief,
        )
        self.assertNotIn(
            "上一轮提供的设计描述已经保留",
            recovered_output["assistant_message"],
        )
        self.assertTrue(str(recovered_output.get("student_task") or "").strip())
        next_pending = current_pending_action(recovered)
        self.assertEqual(next_pending["subject"], "research_object")
        self.assertFalse(
            str(next_pending.get("candidate_answer") or "").strip()
        )

        requested = engine.process_turn(design_id, {"message": "建立新的实验方向"})
        after_request = engine.store.get(design_id)
        self.assertFalse(
            str(after_request.design_context["idea"].get("original") or "").strip()
        )
        self.assertTrue(after_request.design_context["emvr_design"]["awaiting_new_topic"])
        self.assertNotIn(
            "建立新的实验方向",
            str(after_request.design_context),
        )
        self.assertTrue(str(requested["student_task"] or "").strip())
        self.assertEqual(
            current_pending_action(after_request)["answer_fields"],
            ["experiment_brief"],
        )

        engine.process_turn(design_id, {"message": second_brief})
        supplied = engine.store.get(design_id)
        requirements = merge_emvr_structured_requirements(
            supplied.design_context["emvr_design"]
        )
        self.assertEqual(requirements["experiment_brief"], second_brief)
        self.assertEqual(requirements["research_object"], "两个点电荷")
        self.assertEqual(
            requirements["changed_quantities"],
            ["两个点电荷的间距", "电荷极性配置"],
        )
        self.assertEqual(
            requirements["observed_quantities"],
            ["中间区域电场线的弯曲和连接变化"],
        )

        engine.process_turn(
            design_id,
            {"message": "把完整设计方向改得更准确，但保留已经拆出的字段。"},
        )
        revised = engine.store.get(design_id)
        revised_requirements = merge_emvr_structured_requirements(
            revised.design_context["emvr_design"]
        )
        self.assertEqual(revised_requirements["experiment_brief"], revised_brief)
        self.assertEqual(revised_requirements["research_object"], "两个点电荷")
        self.assertNotIn("brief_revisions", revised.design_context["emvr_design"])

        engine.process_turn(design_id, {"message": "确认并进入下一阶段"})
        advanced = engine.store.get(design_id)
        self.assertEqual(advanced.current_stage, Stage.COURSE_MAPPING_AND_DIRECTION)

    def test_guided_new_topic_does_not_create_emvr_state(self) -> None:
        new_topic = "比较不同介质边界附近的电场分布"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "NEW_TOPIC_CONTENT",
                        "target": "experiment_direction",
                        "operation": "EXECUTE",
                        "content": new_topic,
                        "confidence": 0.99,
                    }
                ],
                semantic_updates={"topic_change_explicit": True},
            )
        )
        session = variable_stage_session("design_guided_topic_isolation")
        engine.store.save(session)

        engine.process_turn(session.design_id, {"message": f"换成{new_topic}"})

        stored = engine.store.get(session.design_id)
        self.assertEqual(stored.design_context["idea"]["original"], new_topic)
        self.assertNotIn("emvr_design", stored.design_context)

    def test_invalid_empty_new_topic_act_cannot_reset_the_design(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "NEW_TOPIC",
                        "target": "experiment_direction",
                        "operation": "EXECUTE",
                        "content": "",
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = variable_stage_session("design_reject_empty_new_topic")
        original_idea = session.design_context["idea"]["main_direction"]
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "我想换一个方向，但还没有说具体内容。"},
        )

        stored = engine.store.get(session.design_id)
        self.assertEqual(stored.current_stage, Stage.VARIABLES_AND_CONDITIONS)
        self.assertEqual(stored.design_context["idea"]["main_direction"], original_idea)
        self.assertTrue(result["stage_payload"]["clarification_required"])

    def test_trail9_confirmation_reuses_candidate_research_answer(self) -> None:
        class CandidateConfirmationGenerator(RuleBasedStageGenerator):
            def __init__(self) -> None:
                self.intent_calls = 0

            def resolve_intent(
                self,
                session,
                user_message,
                pending_action,
                carried_context,
            ):
                self.intent_calls += 1
                if self.intent_calls == 1:
                    return resolved_intent(
                        UserIntent.UNCLEAR,
                        target="research_question",
                        confidence=0.96,
                        source="SEMANTIC_TEST",
                        semantic_updates={},
                    )
                return resolved_intent(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    target="research_question",
                    resolved_value=str(
                        pending_action.get("candidate_answer") or ""
                    ),
                    confidence=0.98,
                    source="SEMANTIC_TEST",
                    semantic_updates={
                        "facet_updates": [
                            {
                                "facet_id": "research_question",
                                "status": "CLEAR",
                                "operation": "REPLACE",
                                "value": str(
                                    pending_action.get("candidate_answer") or ""
                                ),
                            }
                        ]
                    },
                )

        generator = CandidateConfirmationGenerator()
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_trail9_candidate_confirmation")
        engine.store.save(session)
        candidate = (
            "我想比较以下两种条件：当两个点状源带同种电荷靠近时，电场线像是被两边"
            "挤压，而带异种电荷靠近时，更像从一条较直的过渡带开始弯过去"
        )

        first = engine.process_turn(session.design_id, {"message": candidate})
        self.assertTrue(first["stage_payload"]["clarification_required"])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["candidate_answer"], candidate)

        confirmed = engine.process_turn(
            session.design_id,
            {"message": "上一句就是我的研究问题"},
        )

        status = confirmed["stage_payload"]["idea_development_status"]
        self.assertEqual(
            status["facets_by_id"]["research_question"]["status"],
            "CLEAR",
        )
        self.assertEqual(
            status["facets_by_id"]["research_question"]["evidence"],
            candidate,
        )
        self.assertNotIn("不需要再次重写", confirmed["assistant_message"])
        self.assertFalse(confirmed["stage_payload"].get("clarification_required", False))

    def test_trail13_substantive_hypothesis_is_saved_without_repeating_the_question(self) -> None:
        hypothesis = (
            "导体球周围的场线弯曲更明显，因为导体内部场为零，场线只能绕行。"
            "介质球场线会穿进去，弯曲小一些。如果球移近电极，场强变大，弯曲应该"
            "更明显；如果只是旋转球，球形对称所以弯曲程度不变。"
        )
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "hypothesis", "status": "CLEAR"}
                ]
            },
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_trail13_hypothesis")
        development = session.design_context["idea_development"]
        for facet_id in ("research_question", "learning_objective"):
            development["facets"][facet_id].update(
                {
                    "status": "CLEAR",
                    "evidence": "前面已经由学生说明",
                    "source": "STUDENT_SEMANTIC",
                }
            )
        development["facets"]["hypothesis"].update(
            {"status": "MISSING", "evidence": "", "source": None}
        )
        development["facets"]["conceptual_structure"].update(
            {"status": "MISSING", "evidence": "", "source": None}
        )
        development["active_facet_id"] = "hypothesis"
        development["missing_facet_ids"] = ["hypothesis", "conceptual_structure"]
        development["complete"] = False
        save_pending_action(
            session,
            Stage.IDEA_BRAINSTORMING,
            build_gap_output(session, ""),
        )
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": hypothesis})

        status = result["stage_payload"]["idea_development_status"]
        self.assertEqual(status["facets_by_id"]["hypothesis"]["status"], "CLEAR")
        self.assertEqual(status["facets_by_id"]["hypothesis"]["evidence"], hypothesis)
        self.assertEqual(status["active_facet_id"], "conceptual_structure")
        self.assertIn("你的预测", result["assistant_message"])
        self.assertIn("实验中需要出现的对象和关系", result["assistant_message"])
        self.assertNotIn("预计关键条件发生变化时", result["assistant_message"])

    def test_trail14_scene_choice_and_own_idea_lock_direction_in_one_turn(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design(
            "我想探究静电场，看看不同物体的场线分布和相互影响"
        )
        selected = first["stage_payload"]["exploration_scenes"][0]["course_anchor"]
        detail = "我对导体和介质在同样电场下的场线区别感兴趣"
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "selected_option_ids": [selected["option_id"]],
                "course_scope_status": "COURSE_CONTENT",
                "stage_one_direction_detail": detail,
            },
        )
        engine.generator = generator

        result = engine.process_turn(
            first["design_id"],
            {"message": f"可以基于图景A展开。{detail}"},
        )

        self.assertEqual(result["stage_payload"]["brainstorm_phase"], "DEPTH_EXPANSION")
        self.assertEqual(result["stage_payload"]["exploration_scenes"], [])
        self.assertEqual(result["stage_payload"]["alternative_ideas"], [])
        self.assertTrue(result["stage_payload"]["direction_locked"])
        self.assertEqual(result["stage_payload"]["core_phenomenon"], detail)
        self.assertEqual(
            result["stage_payload"]["selected_course_relations"],
            [selected],
        )
        self.assertNotIn("下面不是一组标准答案", result["assistant_message"])
        self.assertNotIn("你可以描述让你注意到它的现象", result["assistant_message"])
        self.assertIn("实验大纲雏形", result["assistant_message"])
        stored = engine.store.get(first["design_id"])
        self.assertTrue(stored.design_context["idea"]["direction_locked"])
        self.assertIn("idea_development", stored.design_context)

    def test_scene_reference_control_cannot_override_substantive_direction_content(self) -> None:
        """A long scene response is content, even if the parser also emits REQUEST_REFERENCE."""

        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.REQUEST_MORE_EXAMPLES,
                target="exploration_scenes",
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "selected_option_ids": ["scene_option_17"],
                    "stage_one_direction_detail": (
                        "我想沿着导体与介质在同一外加电场中的场线分布差异继续，"
                        "并重点比较材料边界附近的弯曲。"
                    ),
                    "stage_one_scene_response": "SELECT_OR_DEVELOP",
                },
                dialogue_acts=[
                    {
                        "type": "REQUEST_REFERENCE",
                        "target": "exploration_scenes",
                        "operation": "EXECUTE",
                        "content": "",
                        "confidence": 0.92,
                    }
                ],
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
        )
        self.assertEqual(
            resolved["semantic_updates"]["selected_option_ids"],
            ["scene_option_17"],
        )
        self.assertIn(
            "材料边界附近",
            resolved["semantic_updates"]["stage_one_direction_detail"],
        )
        self.assertEqual(
            resolved["semantic_updates"]["stage_one_scene_response"],
            "SELECT_OR_DEVELOP",
        )

    def test_scene_continuation_without_detail_overrides_misclassified_reference(self) -> None:
        """A scene choice remains a continuation even when no new physics detail is added."""

        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.REQUEST_MORE_EXAMPLES,
                target="exploration_scenes",
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "selected_option_ids": ["latest_scene_b"],
                    "stage_one_scene_response": "SELECT_OR_DEVELOP",
                },
                dialogue_acts=[
                    {
                        "type": "REQUEST_REFERENCE",
                        "target": "exploration_scenes",
                        "operation": "EXECUTE",
                        "content": "",
                        "confidence": 0.92,
                    }
                ],
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
        )
        self.assertEqual(
            resolved["semantic_updates"]["selected_option_ids"],
            ["latest_scene_b"],
        )

    def test_scene_selection_resolver_receives_the_latest_visible_batch(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design("我想探索静电场中的物体相互影响")
        scenes = first["stage_payload"]["exploration_scenes"]
        latest_b = scenes[1]["course_anchor"]
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "selected_option_ids": [latest_b["option_id"]],
                "stage_one_scene_response": "SELECT_OR_DEVELOP",
                "course_scope_status": "COURSE_CONTENT",
            },
        )
        engine.generator = generator

        result = engine.process_turn(
            first["design_id"],
            {"message": "我想沿刚才的图景B继续展开"},
        )

        visible = generator.calls[0]["carried_context"][
            "latest_exploration_scenes"
        ]
        self.assertEqual(
            [item["option_id"] for item in visible],
            [scene["course_anchor"]["option_id"] for scene in scenes],
        )
        self.assertEqual(
            result["stage_payload"]["selected_course_relations"],
            [latest_b],
        )
        self.assertEqual(result["stage_payload"]["exploration_scenes"], [])
        self.assertNotIn("下面不是一组标准答案", result["assistant_message"])

    def test_scene_choice_ends_directionless_browsing_instead_of_replaying_scenes(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design("我想先浏览课程方向")
        scenes = first["stage_payload"].get("exploration_scenes", [])
        if not scenes:
            # Recreate the exact persisted state produced by a directionless
            # breadth turn without coupling this regression to lexical input
            # classification in the offline generator.
            first = engine.create_design("我想探索静电场中的物体相互影响")
            scenes = first["stage_payload"]["exploration_scenes"]
        session = engine.store.get(first["design_id"])
        session.design_context.setdefault("idea", {})[
            "directionless_browse_active"
        ] = True
        engine.store.save(session)
        selected = scenes[0]["course_anchor"]
        generator = ScriptedSemanticGenerator(
            UserIntent.REQUEST_MORE_EXAMPLES,
            target="exploration_scenes",
            semantic_updates={
                "selected_option_ids": [selected["option_id"]],
                "course_scope_status": "COURSE_CONTENT",
                "stage_one_scene_response": "SELECT_OR_DEVELOP",
                "control_actions": ["REQUEST_REFERENCE"],
            },
        )
        engine.generator = generator

        result = engine.process_turn(
            first["design_id"],
            {"message": "这个图景比较典型，我想沿这个方向继续"},
        )

        self.assertEqual(result["stage_payload"]["exploration_scenes"], [])
        self.assertEqual(result["stage_payload"]["alternative_ideas"], [])
        self.assertTrue(result["stage_payload"]["direction_locked"])
        self.assertNotIn("下面不是一组标准答案", result["assistant_message"])
        stored = engine.store.get(first["design_id"])
        self.assertNotIn(
            "directionless_browse_active",
            stored.design_context.get("idea", {}),
        )

    def test_new_scene_batch_requires_scene_specific_semantic_decision(self) -> None:
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.REQUEST_MORE_EXAMPLES,
                target="exploration_scenes",
                confidence=0.98,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "stage_one_scene_response": "REQUEST_NEW_BATCH",
                    "scene_batch_authorized": True,
                },
                dialogue_acts=[
                    {
                        "type": "REQUEST_REFERENCE",
                        "target": "exploration_scenes",
                        "operation": "EXECUTE",
                        "content": "",
                        "confidence": 0.98,
                    }
                ],
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.REQUEST_MORE_EXAMPLES.value
        )
        self.assertEqual(
            resolved["semantic_updates"]["stage_one_scene_response"],
            "REQUEST_NEW_BATCH",
        )

    def test_locked_direction_turns_scene_request_into_current_facet_reference(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())
        first = engine.create_design("我想探索静电场中的材料响应")
        selected = first["stage_payload"]["exploration_scenes"][0]["course_anchor"]
        detail = "比较相同外形的导体和介质在同一外加场中的场线分布"
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "selected_option_ids": [selected["option_id"]],
                "course_scope_status": "COURSE_CONTENT",
                "stage_one_direction_detail": detail,
            },
        )
        engine.generator = generator
        engine.process_turn(
            first["design_id"],
            {"message": f"我选这个图景，并想{detail}"},
        )

        generator.intent = UserIntent.REQUEST_MORE_EXAMPLES
        generator.target = "exploration_scenes"
        generator.semantic_updates = {"course_scope_status": "COURSE_CONTENT"}
        reference = engine.process_turn(
            first["design_id"],
            {"message": "我想先看一个贴合当前方向的参考"},
        )

        self.assertEqual(reference["stage_payload"].get("exploration_scenes", []), [])
        self.assertNotIn("图景 A｜", reference["assistant_message"])
        self.assertIn("不重新换题", reference["assistant_message"])

    def test_confirmed_candidate_answer_generalizes_to_later_guided_stage(self) -> None:
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.VARIABLES_AND_CONDITIONS.value,
            "candidate_answer": "主动改变距离，观察场线并保持电荷量不变",
            "candidate_binding_authorized": True,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.UNCLEAR.value,
            ],
        }

        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                confidence=0.98,
                source="SEMANTIC_TEST",
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
        )
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"], "CLEAR"
        )
        self.assertEqual(
            resolved["resolved_value"], pending["candidate_answer"]
        )
        self.assertEqual(resolved["source"], "CONFIRMED_PENDING_ANSWER")

    def test_confirmed_candidate_answer_also_closes_emvr_open_question(self) -> None:
        """EMVR uses the same contextual open-answer protocol as guided mode."""

        pending = {
            "type": "ANSWER_EMVR_STAGE_QUESTION",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": "experiment_brief",
            "answer_fields": ["experiment_brief"],
            "candidate_answer": (
                "在VR中拖动两个带电物体改变距离，观察导体与介质附近的电场线变化"
            ),
            "candidate_binding_authorized": True,
            "allowed_intents": [
                UserIntent.ANSWER_CURRENT_QUESTION.value,
                UserIntent.REQUEST_MORE_EXAMPLES.value,
                UserIntent.UNCLEAR.value,
            ],
        }

        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                confidence=0.98,
                source="SEMANTIC_TEST",
                dialogue_acts=[],
                actions_authoritative=True,
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value
        )
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"], "CLEAR"
        )
        self.assertEqual(
            resolved["resolved_value"], pending["candidate_answer"]
        )
        self.assertEqual(resolved["source"], "CONFIRMED_PENDING_ANSWER")

    def test_emvr_parse_failure_does_not_bind_a_narrow_pending_field_by_default(self) -> None:
        """A failed parser cannot copy a whole mixed answer into a narrow field."""

        cases = (
            (
                "parameter_specifications",
                ["距离 0.2–2.0 m，步长 0.1 m"],
                "stage",
            ),
            (
                "research_question",
                "距离由远到近时，两种极性配置的中间场线如何变化？",
                "design",
            ),
            (
                "theoretical_framework",
                "使用库仑定律与电场叠加解释空间场线变化。",
                "design",
            ),
        )
        for field, answer, state_kind in cases:
            with self.subTest(field=field):
                session = DesignSession(
                    design_id=f"recover_{field}",
                    interaction_state=InteractionState.EMVR_DIRECT,
                    design_context={"emvr_design": {}},
                )
                save_pending_action(
                    session,
                    Stage.VARIABLES_AND_CONDITIONS,
                    StepOutput(
                        assistant_message="请补充这一项。",
                        stage_payload={
                            "pending_action": {
                                "type": "ANSWER_EMVR_STAGE_QUESTION",
                                "interaction_state": InteractionState.EMVR_DIRECT.value,
                                "subject": field,
                                "answer_fields": [field],
                                "question": "请补充这一项。",
                                "allowed_intents": [
                                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                                    UserIntent.UNCLEAR.value,
                                ],
                            }
                        },
                        student_task="请补充这一项。",
                    ),
                )
                candidate_text = (
                    answer if isinstance(answer, str) else "；".join(answer)
                )
                pending = record_pending_clarification(
                    session,
                    candidate_text,
                    allow_exact_field_binding=False,
                )
                assert pending is not None
                self.assertFalse(pending["candidate_binding_authorized"])
                if field == "parameter_specifications":
                    candidate_text = "距离 0.1–1.5 m，步长 0.05 m"
                    pending = record_pending_clarification(
                        session,
                        candidate_text,
                        allow_exact_field_binding=False,
                    )
                    assert pending is not None
                    self.assertEqual(
                        pending["candidate_answer"],
                        "距离 0.2–2.0 m，步长 0.1 m",
                    )

                resolved = validate_resolved_intent(
                    resolved_intent(
                        UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                        confidence=0.99,
                        source="SEMANTIC_TEST",
                        dialogue_acts=[],
                        actions_authoritative=True,
                    ),
                    pending,
                )
                self.assertEqual(
                    resolved["intent"],
                    UserIntent.UNCLEAR.value,
                )
                apply_resolved_intent(
                    session,
                    resolved,
                    pending,
                    user_message="确认沿用",
                )

                if state_kind == "stage":
                    self.assertFalse(
                        str(stage_design_state_snapshot(session).get(field) or "").strip()
                    )
                else:
                    self.assertFalse(
                        str(design_state_snapshot(session).get(field) or "").strip()
                    )

    def test_confirmation_question_recovers_a_substantive_revision_by_context(self) -> None:
        supplement = (
            "保持电荷配置不变，增加导体与介质材料切换，并比较界面附近的场线"
        )
        pending = {
            "type": "CONFIRM_STAGE_OR_MODIFY",
            "interaction_state": InteractionState.EMVR_DIRECT.value,
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "proposal": {"stage": Stage.IDEA_BRAINSTORMING.value},
            "question": "请补充或修改；如果草稿准确也可以继续。",
            "allowed_intents": [
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                UserIntent.UNCLEAR.value,
            ],
        }
        session = DesignSession(
            design_id="emvr_confirmation_candidate",
            interaction_state=InteractionState.EMVR_DIRECT,
            model_context={"dialogue_state": {"pending_action": pending}},
        )
        stored = record_pending_clarification(session, supplement)
        assert stored is not None
        self.assertEqual(stored["candidate_answer"], supplement)
        self.assertEqual(
            stored["candidate_resolution"],
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        )

        # The semantic layer may call a substantive draft response an ANSWER;
        # the state machine normalizes it to a modification from pending type.
        normalized = validate_resolved_intent(
            resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                resolved_value=supplement,
                confidence=0.97,
                source="SEMANTIC_TEST",
            ),
            stored,
        )
        self.assertEqual(
            normalized["intent"], UserIntent.MODIFY_PREVIOUS_PROPOSAL.value
        )
        self.assertEqual(normalized["resolved_value"], supplement)

        accepted_candidate = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                resolved_value=None,
                confidence=0.98,
                source="SEMANTIC_TEST",
            ),
            stored,
        )
        self.assertEqual(
            accepted_candidate["intent"],
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
        )
        self.assertIsNone(accepted_candidate["resolved_value"])

        # A raw candidate retained after an unclear parse is not a field-level
        # update. A later summary intent cannot make that raw paragraph safe.
        recovered = validate_resolved_intent(
            resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                resolved_value=None,
                confidence=0.98,
                source="SEMANTIC_TEST",
            ),
            stored,
        )
        self.assertIsNone(recovered["resolved_value"])
        self.assertEqual(recovered["source"], "SEMANTIC_TEST")

    def test_guided_confirmation_recovers_the_same_saved_revision(self) -> None:
        candidate = "补充一个分层介质条件，原有材料对照保持不变"
        pending = {
            "type": "CONFIRM_STAGE_OR_MODIFY",
            "interaction_state": InteractionState.GUIDED_DESIGN.value,
            "subject": Stage.VARIABLES_AND_CONDITIONS.value,
            "proposal": {"controlled_conditions": ["几何尺寸"]},
            "candidate_answer": candidate,
            "candidate_resolution": UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
            "allowed_intents": [
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                UserIntent.UNCLEAR.value,
            ],
        }

        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                confidence=0.98,
                source="SEMANTIC_TEST",
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"], UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value
        )
        self.assertIsNone(resolved["resolved_value"])
        self.assertEqual(resolved["source"], "SEMANTIC_TEST")

    def test_missing_facet_decision_recovers_without_questioning_student_intent(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={"facet_updates": [], "comparison_updates": []},
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_missing_facet_decision")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {
                "message": (
                    "逐渐缩短同种或异种电荷源之间的距离，观察场线形状和空间分布"
                )
            },
        )

        self.assertFalse(result["stage_payload"].get("clarification_required", False))
        self.assertIn("研究问题已经很具体", result["assistant_message"])
        self.assertNotIn("还没有准确判断", result["assistant_message"])
        stored = engine.get_design(session.design_id)["design_context"][
            "idea_development"
        ]
        self.assertEqual(stored["facets"]["research_question"]["status"], "CLEAR")

    def test_semantic_omission_is_recovered_on_first_answer(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={"facet_updates": [], "comparison_updates": []},
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_repeated_semantic_omission")
        engine.store.save(session)

        first = engine.process_turn(
            session.design_id,
            {"message": "缩短两个源的距离，观察场线空间分布"},
        )
        self.assertFalse(first["stage_payload"].get("clarification_required", False))
        self.assertIn("研究问题已经很具体", first["assistant_message"])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertNotEqual(pending["subject"], "research_question")

    def test_comparison_only_edit_does_not_have_to_answer_active_facet(self) -> None:
        session = idea_facet_session("design_separate_comparison_edit")
        pending = current_pending_action(session)
        assert pending is not None
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                target="polarity_cases",
                confidence=0.97,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "comparison_updates": [
                        {
                            "comparison_id": "polarity_cases",
                            "action": "MODIFY",
                            "cases": ["同种电荷"],
                        }
                    ]
                },
            ),
            pending,
        )

        self.assertEqual(
            resolved["intent"],
            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
        )
        self.assertEqual(
            resolved["semantic_updates"]["comparison_updates"][0]["cases"],
            ["同种电荷"],
        )

    def test_repeated_explicit_missing_facet_uses_non_repeating_prompts(self) -> None:
        generator = ScriptedSemanticGenerator(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "research_question", "status": "MISSING"}
                ]
            },
        )
        engine = WorkflowEngine(generator=generator)
        session = idea_facet_session("design_repeated_missing_facet")
        engine.store.save(session)

        first = engine.process_turn(
            session.design_id,
            {"message": "我还没说清楚，暂时只想到两个电荷源"},
        )
        second = engine.process_turn(
            session.design_id,
            {"message": "现在还是不知道怎样写研究问题"},
        )

        self.assertTrue(first["stage_payload"]["reference_only"])
        self.assertTrue(second["stage_payload"]["reference_only"])
        self.assertEqual(first["stage_payload"]["reference_for_facet"], "research_question")
        self.assertEqual(second["stage_payload"]["reference_for_facet"], "research_question")
        self.assertNotIn("图景 A", first["assistant_message"])
        self.assertNotEqual(first["assistant_message"], second["assistant_message"])

    def test_semantic_comparison_update_uses_validated_ids_and_cases(self) -> None:
        session = DesignSession(
            design_id="design_semantic_comparison",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "standard_comparisons": [
                        {
                            "comparison_id": "polarity_cases",
                            "recommended_cases": ["同种", "异种"],
                            "cases": ["同种", "异种"],
                            "adoption_status": "PENDING",
                        }
                    ]
                }
            },
        )
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            confidence=0.96,
            source="SEMANTIC_TEST",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "polarity_cases",
                        "action": "MODIFY",
                        "cases": ["同种", "不存在的情形"],
                    }
                ]
            },
        )

        apply_resolved_intent(session, decision, None, "沿用第一种，另一种先不放进来")

        comparison = session.design_context["idea"]["standard_comparisons"][0]
        self.assertEqual(comparison["cases"], ["同种"])
        self.assertEqual(comparison["adoption_status"], "MODIFIED")

    def test_student_can_append_a_new_comparison_case_without_model_invention(self) -> None:
        original_cases = [
            "较小闭合曲面完整包住场源",
            "较大闭合曲面完整包住场源",
            "不规则闭合曲面完整包住场源",
        ]
        new_case = "曲面没有完全包住场源"
        session = DesignSession(
            design_id="design_student_comparison_addition",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "standard_comparisons": [
                        {
                            "comparison_id": "surface_enclosure_cases",
                            "recommended_cases": original_cases,
                            "cases": list(original_cases),
                            "adoption_status": "ACCEPTED",
                        }
                    ]
                }
            },
        )
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            confidence=0.98,
            source="SEMANTIC_TEST",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "surface_enclosure_cases",
                        "action": "MODIFY",
                        "cases": [new_case, "模型自行虚构的第五种情形"],
                        "merge_with_existing": True,
                    }
                ]
            },
        )

        apply_resolved_intent(
            session,
            decision,
            None,
            f"请补充第四种参照情形：{new_case}，其他内容不变。",
        )

        comparison = session.design_context["idea"]["standard_comparisons"][0]
        self.assertEqual(comparison["cases"], [*original_cases, new_case])
        self.assertNotIn("模型自行虚构的第五种情形", comparison["cases"])
        self.assertEqual(comparison["adoption_status"], "MODIFIED")

        initialize_idea_development(
            session,
            {"core_phenomenon": "比较闭合曲面的局部场强和总通量"},
        )
        session.turn_context = {"resolved_intent": decision}
        visible = build_gap_output(session, new_case)
        self.assertIn("对照调整已经并入", visible.assistant_message)
        self.assertIn(new_case, visible.assistant_message)
        self.assertNotIn("还需要把它与当前实验想法", visible.assistant_message)

    def test_semantic_case_refs_replace_paraphrases_without_duplicates(self) -> None:
        session = DesignSession(
            design_id="design_semantic_case_identity",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "standard_comparisons": [
                        {
                            "comparison_id": "gauss_enclosure_cases",
                            "recommended_cases": [
                                "曲面完全包住同一场源",
                                "曲面只包住部分场源区域",
                            ],
                            "cases": [
                                "曲面完全包住同一场源",
                                "曲面只包住部分场源区域",
                                "曲面完全包住场源",
                                "只包住部分场源",
                            ],
                            "case_aliases": {},
                            "adoption_status": "MODIFIED",
                        }
                    ]
                }
            },
        )
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            confidence=0.99,
            source="SEMANTIC_TEST",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "gauss_enclosure_cases",
                        "action": "MODIFY",
                        "case_refs": [
                            "gauss_enclosure_cases:case:1",
                            "gauss_enclosure_cases:case:2",
                        ],
                        "renames": [
                            {
                                "case_ref": "gauss_enclosure_cases:case:1",
                                "label": "曲面完全包住场源",
                            },
                            {
                                "case_ref": "gauss_enclosure_cases:case:2",
                                "label": "曲面只包住部分场源",
                            },
                        ],
                        "replace_all": True,
                    }
                ]
            },
        )

        apply_resolved_intent(
            session,
            decision,
            None,
            "把基础比较精简为曲面完全包住场源、曲面只包住部分场源。",
        )

        comparison = session.design_context["idea"]["standard_comparisons"][0]
        self.assertEqual(
            comparison["cases"],
            ["曲面完全包住场源", "曲面只包住部分场源"],
        )
        self.assertEqual(comparison["adoption_status"], "MODIFIED")

    def test_confirmed_clarification_keeps_original_student_evidence(self) -> None:
        original_cases = ["导体", "均匀介质"]
        new_case = "分层介质"
        session = DesignSession(
            design_id="design_confirmed_comparison_evidence",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "standard_comparisons": [
                        {
                            "comparison_id": "material_cases",
                            "recommended_cases": original_cases,
                            "cases": list(original_cases),
                            "adoption_status": "ACCEPTED",
                        }
                    ]
                }
            },
        )
        decision = resolved_intent(
            UserIntent.MODIFY_PREVIOUS_PROPOSAL,
            resolved_value=f"再加入{new_case}，其他情况保留",
            confidence=0.98,
            source="CONFIRMED_PENDING_MODIFICATION",
            semantic_updates={
                "comparison_updates": [
                    {
                        "comparison_id": "material_cases",
                        "action": "MODIFY",
                        "cases": [new_case, "模型虚构的材料"],
                        "merge_with_existing": True,
                    }
                ]
            },
        )

        # The current message only confirms the previous clarification.  The
        # saved resolved_value is the authoritative student-authored evidence.
        apply_resolved_intent(session, decision, None, "对，就是这个补充")

        comparison = session.design_context["idea"]["standard_comparisons"][0]
        self.assertEqual(comparison["cases"], [*original_cases, new_case])
        self.assertNotIn("模型虚构的材料", comparison["cases"])

    def test_facet_merge_preserves_previous_answer_and_adds_supplement(self) -> None:
        session = DesignSession(
            design_id="design_facet_merge",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={"idea": {"original": "比较闭合曲面上的电通量"}},
        )
        development = initialize_idea_development(
            session,
            {"core_phenomenon": "比较闭合曲面的局部场强和总通量"},
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "conceptual_structure", "status": "CLEAR"}
                ]
            },
        )
        original = development["facets"]["conceptual_structure"]["evidence"]

        update_idea_development(
            session,
            "补充一个没有完全包住场源的闭合曲面对照",
            semantic_updates={
                "facet_updates": [
                    {
                        "facet_id": "conceptual_structure",
                        "status": "CLEAR",
                        "operation": "MERGE",
                    }
                ]
            },
        )

        evidence = development["facets"]["conceptual_structure"]["evidence"]
        self.assertIn(original, evidence)
        self.assertIn("没有完全包住场源", evidence)

    def test_semantic_facet_updates_do_not_erase_confirmed_facets(self) -> None:
        session = DesignSession(
            design_id="design_semantic_facets",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={"idea": {"original": "研究两个场源的空间叠加"}},
        )
        development = initialize_idea_development(
            session,
            {"core_phenomenon": "两个场源靠近时空间分布发生变化"},
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "learning_objective", "status": "CLEAR"}
                ]
            },
        )
        self.assertEqual(development["facets"]["learning_objective"]["status"], "CLEAR")
        self.assertEqual(development["facets"]["research_question"]["status"], "MISSING")

        update_idea_development(
            session,
            "我想比较两种间距，并观察中间区域场分布如何变化",
            semantic_updates={
                "facet_updates": [
                    {"facet_id": "research_question", "status": "CLEAR"},
                    {"facet_id": "learning_objective", "status": "MISSING"},
                ]
            },
        )
        self.assertEqual(development["facets"]["research_question"]["status"], "CLEAR")
        self.assertEqual(development["facets"]["learning_objective"]["status"], "CLEAR")

    def test_semantic_advance_paraphrases_use_one_state_transition(self) -> None:
        paraphrases = (
            "继续往下整理",
            "可以进行后面的内容了",
            "这部分就这样，接着做",
            "不用改，继续后面的内容",
            "沿用刚才的安排并往后走",
            "当前部分没问题，可以向下完善",
            "保留现有内容，开始下一部分",
            "按你上面说的处理，然后继续",
        )
        for index, message in enumerate(paraphrases):
            with self.subTest(message=message):
                generator = ScriptedSemanticGenerator(UserIntent.ADVANCE_STAGE)
                engine = WorkflowEngine(generator=generator)
                session = variable_stage_session(f"design_semantic_advance_{index}")
                engine.store.save(session)

                result = engine.process_turn(session.design_id, {"message": message})

                self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_PROCEDURE.value)
                self.assertEqual(
                    result["transitioned_from_stage"],
                    Stage.VARIABLES_AND_CONDITIONS.value,
                )
                self.assertIn("两个源之间的距离", result["assistant_message"])
                self.assertIn("电场线形状", result["assistant_message"])
                self.assertIn("源强", result["assistant_message"])
                self.assertEqual(len(generator.calls), 1)
                self.assertIn("pending_action", generator.calls[0])

    def test_accept_modify_reject_update_the_pending_subject(self) -> None:
        for intent, expected in (
            (UserIntent.ACCEPT_PREVIOUS_PROPOSAL, "ACCEPTED"),
            (UserIntent.MODIFY_PREVIOUS_PROPOSAL, "MODIFIED"),
            (UserIntent.REJECT_PREVIOUS_PROPOSAL, "REJECTED"),
        ):
            with self.subTest(intent=intent.value):
                session = variable_stage_session(f"design_{intent.value.casefold()}")
                pending = current_pending_action(session)
                assert pending is not None
                value = {"changed": True} if intent is UserIntent.MODIFY_PREVIOUS_PROPOSAL else None
                decision = resolved_intent(
                    intent,
                    target=pending["subject"],
                    resolved_value=value,
                    confidence=0.95,
                    source="SEMANTIC_TEST",
                )

                apply_resolved_intent(session, decision, pending)

                self.assertEqual(
                    session.model_context["dialogue_state"]["pending_action"]["status"],
                    expected,
                )
                subject = pending["subject"]
                self.assertIn(subject, build_carried_context(session)["resolved_decisions"])

    def test_low_confidence_only_asks_one_clarifying_question(self) -> None:
        generator = ScriptedSemanticGenerator(UserIntent.ADVANCE_STAGE, confidence=0.31)
        engine = WorkflowEngine(generator=generator)
        session = variable_stage_session("design_low_confidence")
        engine.store.save(session)

        result = engine.process_turn(session.design_id, {"message": "就按那个来吧"})

        self.assertEqual(result["current_stage"], Stage.VARIABLES_AND_CONDITIONS.value)
        self.assertTrue(result["stage_payload"]["clarification_required"])
        self.assertNotIn("参考结构", result["assistant_message"])
        self.assertNotIn("pending_action", result["assistant_message"])
        self.assertNotIn("你刚才是在", result["assistant_message"])
        self.assertNotIn("请说明上一句", result["assistant_message"])

    def test_carried_context_is_structured_and_internal_state_is_not_public(self) -> None:
        session = variable_stage_session("design_internal_state")
        carried = build_carried_context(session)
        self.assertIn("两个源之间的距离", carried["independent_variable"])
        self.assertIn("电场线形状", carried["observations"])
        self.assertIn("源强", carried["controlled_conditions"])

        public = session.to_dict()
        self.assertNotIn("dialogue_state", public["design_context"])
        self.assertNotIn("pending_action", str(public))

    def test_state_machine_rejects_disallowed_or_low_confidence_intent(self) -> None:
        session = variable_stage_session("design_validation")
        pending = current_pending_action(session)
        assert pending is not None
        pending["allowed_intents"] = [UserIntent.ANSWER_CURRENT_QUESTION.value]
        invalid = validate_resolved_intent(
            resolved_intent(
                UserIntent.ADVANCE_STAGE,
                confidence=0.99,
                source="SEMANTIC_TEST",
            ),
            pending,
        )
        self.assertEqual(invalid["intent"], UserIntent.UNCLEAR.value)

    def test_low_confidence_discards_structured_design_updates(self) -> None:
        session = DesignSession(
            design_id="design_low_confidence_update",
            interaction_state=InteractionState.GUIDED_DESIGN,
            design_context={
                "idea": {
                    "standard_comparisons": [
                        {
                            "comparison_id": "baseline_cases",
                            "recommended_cases": ["甲", "乙"],
                            "cases": ["甲", "乙"],
                            "adoption_status": "PENDING",
                        }
                    ]
                }
            },
        )
        invalid = validate_resolved_intent(
            resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                confidence=0.2,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "comparison_updates": [
                        {
                            "comparison_id": "baseline_cases",
                            "action": "MODIFY",
                            "cases": ["甲"],
                        }
                    ]
                },
            ),
            None,
        )

        apply_resolved_intent(session, invalid, None)

        comparison = session.design_context["idea"]["standard_comparisons"][0]
        self.assertEqual(comparison["adoption_status"], "PENDING")
        self.assertEqual(comparison["cases"], ["甲", "乙"])

    def test_multi_act_notice_uses_interactive_guided_language(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_DESIGN_FIELD",
                        "target": "learning_objective",
                        "operation": "REPLACE",
                        "content": "解释距离变化如何改变电场线分布",
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = idea_facet_session("design_guided_tone")
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "我希望最后能解释距离变化如何改变电场线分布。"},
        )

        self.assertIn("接到现有想法里", result["assistant_message"])
        self.assertNotIn("已同步修订设计中的", result["assistant_message"])
        self.assertNotIn("设计字段", result["assistant_message"])

    def test_multi_act_notice_uses_professional_emvr_language(self) -> None:
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "unity_objects",
                        "operation": "MERGE",
                        "content": ["点电荷源", "场线可视化对象"],
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = variable_stage_session("design_emvr_tone")
        session.interaction_state = InteractionState.EMVR_DIRECT
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "加入点电荷源和场线可视化对象。"},
        )

        self.assertIn("已同步修订设计中的", result["assistant_message"])
        self.assertIn("VR实验对象", result["assistant_message"])
        self.assertIn("Unity", result["assistant_message"])

    def test_feedback_only_reply_uses_mode_specific_language(self) -> None:
        feedback_act = [
            {
                "type": "CORRECT_ASSISTANT",
                "target": "previous_interpretation",
                "operation": "MERGE",
                "content": "你把我的补充误解成了新方向",
                "confidence": 0.99,
            }
        ]

        guided_engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(feedback_act)
        )
        guided = idea_facet_session("design_guided_feedback_tone")
        guided_engine.store.save(guided)
        guided_result = guided_engine.process_turn(
            guided.design_id,
            {"message": "你把我的补充误解成了新方向。"},
        )

        emvr_engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(feedback_act)
        )
        emvr = variable_stage_session("design_emvr_feedback_tone")
        emvr.interaction_state = InteractionState.EMVR_DIRECT
        emvr_engine.store.save(emvr)
        emvr_result = emvr_engine.process_turn(
            emvr.design_id,
            {"message": "你把我的补充误解成了新方向。"},
        )

        self.assertIn("你提醒得对", guided_result["assistant_message"])
        self.assertIn("我们就从那里接着完善", guided_result["assistant_message"])
        self.assertIn("收到这项校正", emvr_result["assistant_message"])
        self.assertIn("物理模型、Unity交互还是展示方式", emvr_result["assistant_message"])
        self.assertNotIn("字段", emvr_result["assistant_message"])

    def test_later_stage_pending_action_names_canonical_answer_fields(self) -> None:
        expectations = {
            Stage.VARIABLES_AND_CONDITIONS: [
                "independent_variable",
                "observations",
                "controlled_conditions",
            ],
            Stage.CONCEPTUAL_PROCEDURE: ["procedure_steps"],
            Stage.EXPECTED_DATA_VISUALIZATION: ["visualization_plan"],
            Stage.RESULT_INTERPRETATION: ["result_interpretation"],
            Stage.DESIGN_VALUE_AND_LIMITATIONS: ["limitations"],
            Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT: ["student_summary"],
        }
        for stage, expected_fields in expectations.items():
            with self.subTest(stage=stage.value):
                session = DesignSession(
                    design_id=f"design_pending_fields_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=list(Stage).index(stage),
                )
                pending = save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="我们接着完善这一部分。",
                        stage_payload={},
                        student_task="请说说你的想法。",
                    ),
                )
                self.assertEqual(pending["answer_fields"], expected_fields)

    def test_emvr_open_questions_name_canonical_answer_fields(self) -> None:
        expectations = {
            Stage.IDEA_BRAINSTORMING: [
                "research_object",
                "course_relationship",
                "observations",
                "interactions",
                "conceptual_structure",
            ],
            Stage.LEARNING_OBJECTIVES: ["learning_objective"],
            Stage.RESEARCH_QUESTION: ["research_question"],
            Stage.HYPOTHESIS: ["hypothesis", "expected_phenomenon"],
            Stage.CONCEPTUAL_OR_VR_SETUP: [
                "conceptual_structure",
                "unity_objects",
                "interactions",
            ],
            Stage.VARIABLES_AND_CONDITIONS: [
                "independent_variable",
                "observations",
                "controlled_conditions",
            ],
            Stage.CONCEPTUAL_PROCEDURE: ["procedure_steps"],
            Stage.EXPECTED_DATA_VISUALIZATION: ["visualization_plan"],
            Stage.DESIGN_VALUE_AND_LIMITATIONS: ["limitations"],
        }
        for stage, expected_fields in expectations.items():
            with self.subTest(stage=stage.value):
                session = DesignSession(
                    design_id=f"design_emvr_pending_fields_{stage.value}",
                    interaction_state=InteractionState.EMVR_DIRECT,
                    current_stage_index=list(Stage).index(stage),
                )
                pending = save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="请核对本阶段设计。",
                        stage_payload={
                            "pending_action": {
                                "type": "ANSWER_EMVR_STAGE_QUESTION",
                                "interaction_state": InteractionState.EMVR_DIRECT.value,
                                "subject": stage.value,
                                "question": "请补充或修订当前设计。",
                                "allowed_intents": [
                                    UserIntent.ANSWER_CURRENT_QUESTION.value,
                                    UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                                    UserIntent.UNCLEAR.value,
                                ],
                            }
                        },
                        student_task="请补充或修订当前设计。",
                    ),
                )
                self.assertEqual(pending["answer_fields"], expected_fields)

    def test_confirmation_exposes_visible_labels_as_canonical_field_bindings(self) -> None:
        for mode, stage, expected in (
            (
                InteractionState.EMVR_DIRECT,
                Stage.IDEA_BRAINSTORMING,
                {"目标现象": "observations", "可用交互": "interactions"},
            ),
            (
                InteractionState.EMVR_DIRECT,
                Stage.COURSE_MAPPING_AND_DIRECTION,
                {
                    "设计方向": "research_object",
                    "课程关系": "course_relationship",
                    "采用理由": "design_rationale",
                },
            ),
            (
                InteractionState.EMVR_DIRECT,
                Stage.CONCEPTUAL_OR_VR_SETUP,
                {"数据显示": "visualization_plan", "Unity对象": "unity_objects"},
            ),
            (
                InteractionState.GUIDED_DESIGN,
                Stage.VARIABLES_AND_CONDITIONS,
                {"自变量": "independent_variable", "观察量": "observations"},
            ),
            (
                InteractionState.GUIDED_DESIGN,
                Stage.DESIGN_VALUE_AND_LIMITATIONS,
                {"教学价值": "design_value", "设计局限": "limitations"},
            ),
            (
                InteractionState.EMVR_DIRECT,
                Stage.DESIGN_VALUE_AND_LIMITATIONS,
                {"VR附加价值": "design_value", "设计局限": "limitations"},
            ),
        ):
            with self.subTest(mode=mode.value, stage=stage.value):
                session = DesignSession(
                    design_id=f"visible_bindings_{mode.value}_{stage.value}",
                    interaction_state=mode,
                    current_stage_index=list(Stage).index(stage),
                )
                pending = save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="请核对当前草稿。",
                        stage_payload={
                            "pending_action": {
                                "type": "CONFIRM_STAGE_OR_MODIFY",
                                "interaction_state": mode.value,
                                "subject": stage.value,
                                "proposal": {"stage": stage.value},
                            }
                        },
                        student_task="需要补充时直接指出页面中的对应内容。",
                    ),
                )
                bindings = {
                    label: str(item["canonical_field"])
                    for item in pending.get("editable_field_bindings", [])
                    for label in item.get("visible_labels", [])
                }
                for visible_label, canonical_field in expected.items():
                    self.assertEqual(bindings[visible_label], canonical_field)

    def test_emvr_visible_target_phenomenon_supplement_commits_to_both_states(self) -> None:
        supplement = "电场线的空间分布与叠加"
        acts = [
            {
                "type": "MODIFY_STAGE_FIELD",
                "target": "observations",
                "operation": "MERGE",
                "content": supplement,
                "semantic_key": "electric_field_line_spatial_superposition",
                "confidence": 0.99,
            }
        ]
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                acts,
                semantic_updates={
                    "emvr_design_update": {
                        "observed_quantities": [supplement],
                        "field_updates": [
                            {
                                "field_id": "observed_quantities",
                                "operation": "MERGE",
                                "value": [supplement],
                            }
                        ],
                    }
                },
            )
        )
        session = DesignSession(
            design_id="emvr_visible_target_supplement",
            interaction_state=InteractionState.EMVR_DIRECT,
            design_context={
                "idea": {
                    "original": "两个带电物体靠近时观察电场线变化",
                    "main_direction": "两个带电物体靠近时观察电场线变化",
                }
            },
        )
        old_pending = save_pending_action(
            session,
            Stage.IDEA_BRAINSTORMING,
            StepOutput(
                assistant_message="目标现象：静电场。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "interaction_state": InteractionState.EMVR_DIRECT.value,
                        "subject": Stage.IDEA_BRAINSTORMING.value,
                        "proposal": {"target_phenomenon": "静电场"},
                        "advance_on_accept": True,
                    }
                },
                student_task="请核对目标现象，需要补充时直接说明。",
            ),
        )
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"目标现象再补充“{supplement}”。"},
        )

        stored = engine.store.get(session.design_id)
        self.assertIn(
            supplement,
            stage_design_state_snapshot(stored)["observations"],
        )
        self.assertIn(
            supplement,
            merge_emvr_structured_requirements(
                stored.design_context["emvr_design"]
            )["observed_quantities"],
        )
        self.assertIn(supplement, result["assistant_message"])
        new_pending = current_pending_action(stored)
        self.assertIsNotNone(new_pending)
        self.assertNotEqual(
            new_pending.get("action_id"),
            old_pending.get("action_id"),
        )

    def test_guided_visible_observation_supplement_merges_without_reasking(self) -> None:
        supplement = "同时记录中间区域的场线弯曲程度和场强颜色变化"
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "observations",
                        "operation": "MERGE",
                        "content": supplement,
                        "semantic_key": "mid_region_line_bending_and_field_color",
                        "confidence": 0.99,
                    }
                ]
            )
        )
        session = variable_stage_session("guided_visible_observation_supplement")
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "observations",
                    "operation": "REPLACE",
                    "value": ["电场线形状", "中间区域通量"],
                }
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
        )
        save_pending_action(
            session,
            Stage.VARIABLES_AND_CONDITIONS,
            StepOutput(
                assistant_message="观察量：电场线形状和中间区域通量。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "subject": Stage.VARIABLES_AND_CONDITIONS.value,
                        "proposal": {"observations": ["电场线形状", "中间区域通量"]},
                    }
                },
                student_task="需要补充时直接指出对应内容。",
            ),
        )
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": f"观察量还需要补充：{supplement}，其余设置保持不变。"},
        )

        observations = stage_design_state_snapshot(
            engine.store.get(session.design_id)
        )["observations"]
        self.assertIn(supplement, observations)
        self.assertIn("电场线形状", observations)
        self.assertFalse(
            result["stage_payload"].get("clarification_required", False)
        )

    def test_visible_supplements_remain_connected_across_later_stages(self) -> None:
        cases = (
            (
                InteractionState.EMVR_DIRECT,
                Stage.COURSE_MAPPING_AND_DIRECTION,
                "design_rationale",
                "通过空间观察比较场分布，适合用VR呈现不可见场量",
                "design_rationale",
                "selection_reason",
            ),
            (
                InteractionState.EMVR_DIRECT,
                Stage.CONCEPTUAL_OR_VR_SETUP,
                "visualization_plan",
                "同步显示场线、等势面与探针读数",
                "visualization_requirements",
                "visualization_layer",
            ),
            (
                InteractionState.EMVR_DIRECT,
                Stage.DESIGN_VALUE_AND_LIMITATIONS,
                "design_value",
                "利用空间视角比较三维场分布并建立参数与现象的联系",
                "design_values",
                "student_value_and_limit_notes",
            ),
            (
                InteractionState.GUIDED_DESIGN,
                Stage.DESIGN_VALUE_AND_LIMITATIONS,
                "design_value",
                "帮助学生把边界条件与场分布变化联系起来",
                "",
                "student_value_and_limit_notes",
            ),
        )
        for mode, stage, field, supplement, emvr_field, payload_field in cases:
            with self.subTest(mode=mode.value, stage=stage.value, field=field):
                engine = WorkflowEngine(
                    generator=MultiActSemanticGenerator(
                        [
                            {
                                "type": "MODIFY_STAGE_FIELD",
                                "target": field,
                                "operation": "MERGE",
                                "content": supplement,
                                "semantic_key": f"{field}_supplement",
                                "confidence": 0.99,
                            }
                        ]
                    )
                )
                session = DesignSession(
                    design_id=f"visible_supplement_{mode.value}_{stage.value}",
                    interaction_state=mode,
                    current_stage_index=list(Stage).index(stage),
                    design_context={
                        "idea": {
                            "original": "比较不同条件下的电磁场分布",
                            "main_direction": "比较不同条件下的电磁场分布",
                        }
                    },
                )
                save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="请核对当前设计草稿。",
                        stage_payload={
                            "pending_action": {
                                "type": "CONFIRM_STAGE_OR_MODIFY",
                                "interaction_state": mode.value,
                                "subject": stage.value,
                                "proposal": {"stage": stage.value},
                            }
                        },
                        student_task="需要补充时直接指出页面中的对应内容。",
                    ),
                )
                engine.store.save(session)

                result = engine.process_turn(
                    session.design_id,
                    {"message": f"请补充：{supplement}，其余内容保持不变。"},
                )

                stored = engine.store.get(session.design_id)
                self.assertIn(
                    supplement,
                    str(stage_design_state_snapshot(stored)[field]),
                )
                if emvr_field:
                    merged = merge_emvr_structured_requirements(
                        stored.design_context["emvr_design"]
                    )
                    self.assertIn(supplement, str(merged[emvr_field]))
                rendered_payload = result["stage_payload"]
                if payload_field not in rendered_payload:
                    rendered_payload = stored.stage_outputs.get(
                        stage.value, {}
                    ).get("stage_payload", {})
                self.assertIn(supplement, str(rendered_payload[payload_field]))
                self.assertFalse(
                    result["stage_payload"].get("clarification_required", False)
                )

    def test_multiple_supplements_to_one_emvr_field_are_not_collapsed(self) -> None:
        additions = ["增加等势面显示", "增加可移动探针的数值读数"]
        engine = WorkflowEngine(
            generator=MultiActSemanticGenerator(
                [
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "visualization_plan",
                        "operation": "MERGE",
                        "content": addition,
                        "semantic_key": f"visualization_supplement_{index}",
                        "confidence": 0.99,
                    }
                    for index, addition in enumerate(additions)
                ]
            )
        )
        session = DesignSession(
            design_id="multiple_emvr_visualization_supplements",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.CONCEPTUAL_OR_VR_SETUP),
            design_context={"idea": {"main_direction": "比较空间电场分布"}},
        )
        save_pending_action(
            session,
            Stage.CONCEPTUAL_OR_VR_SETUP,
            StepOutput(
                assistant_message="请核对Unity对象和显示方式。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "interaction_state": InteractionState.EMVR_DIRECT.value,
                        "subject": Stage.CONCEPTUAL_OR_VR_SETUP.value,
                        "proposal": {"stage": Stage.CONCEPTUAL_OR_VR_SETUP.value},
                    }
                },
                student_task="可以一次补充多个显示要求。",
            ),
        )
        engine.store.save(session)

        engine.process_turn(
            session.design_id,
            {"message": "请同时增加等势面，并加入可移动探针的数值读数。"},
        )

        stored = engine.store.get(session.design_id)
        merged = merge_emvr_structured_requirements(
            stored.design_context["emvr_design"]
        )["visualization_requirements"]
        for addition in additions:
            self.assertIn(addition, merged)

    def test_each_single_field_guided_stage_commits_answer_and_closes_pending(self) -> None:
        expectations = {
            Stage.CONCEPTUAL_PROCEDURE: (
                "procedure_steps",
                "建立基准、改变距离、记录场线并比较两种极性情形",
            ),
            Stage.EXPECTED_DATA_VISUALIZATION: (
                "visualization_plan",
                "同步显示场线形状、场强颜色和距离读数",
            ),
            Stage.RESULT_INTERPRETATION: (
                "result_interpretation",
                "符合时支持叠加解释，偏离时检查边界设置与显示精度",
            ),
            Stage.DESIGN_VALUE_AND_LIMITATIONS: (
                "limitations",
                "点电荷和连续场线均属于理想化表示，结论受显示分辨率限制",
            ),
        }
        for stage, (field, answer) in expectations.items():
            with self.subTest(stage=stage.value):
                session = DesignSession(
                    design_id=f"design_single_field_commit_{stage.value}",
                    interaction_state=InteractionState.GUIDED_DESIGN,
                    current_stage_index=list(Stage).index(stage),
                )
                pending = save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="我们继续完善这一部分。",
                        stage_payload={},
                        student_task="请说说你的判断。",
                    ),
                )
                resolved = validate_resolved_intent(
                    resolved_intent(
                        UserIntent.ANSWER_CURRENT_QUESTION,
                        target=stage.value,
                        confidence=0.98,
                        source="SEMANTIC_MODEL",
                        dialogue_acts=[
                            {
                                "type": "ANSWER_PENDING_QUESTION",
                                "target": stage.value,
                                "operation": "REPLACE",
                                "content": answer,
                                "confidence": 0.98,
                            }
                        ],
                        actions_authoritative=True,
                    ),
                    pending,
                )

                apply_resolved_intent(session, resolved, pending, answer)

                self.assertEqual(stage_design_state_snapshot(session)[field], answer)
                self.assertIsNone(current_pending_action(session))

    def test_public_stage_answer_binds_to_single_canonical_result_field(self) -> None:
        session = DesignSession(
            design_id="design_result_field_binding",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.RESULT_INTERPRETATION),
        )
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.RESULT_INTERPRETATION.value,
            "answer_fields": ["result_interpretation"],
            "question": "结果符合或偏离预期时可能意味着什么？",
            "allowed_intents": [UserIntent.ANSWER_CURRENT_QUESTION.value],
        }
        answer = "符合时支持场叠加解释；偏离时先检查电荷设置和显示精度。"
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                target=Stage.RESULT_INTERPRETATION.value,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "ANSWER_PENDING_QUESTION",
                        "target": Stage.RESULT_INTERPRETATION.value,
                        "operation": "REPLACE",
                        "content": answer,
                        "confidence": 0.98,
                    }
                ],
                actions_authoritative=True,
            ),
            pending,
        )
        apply_resolved_intent(session, resolved, pending, answer)

        self.assertEqual(
            stage_design_state_snapshot(session)["result_interpretation"],
            answer,
        )
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"],
            "CLEAR",
        )

    def test_multi_field_stage_answer_is_committed_field_by_field(self) -> None:
        session = variable_stage_session("design_variable_field_bundle")
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": Stage.VARIABLES_AND_CONDITIONS.value,
            "answer_fields": [
                "independent_variable",
                "observations",
                "controlled_conditions",
            ],
            "question": "主动改变、观察和保持不变的量分别是什么？",
            "allowed_intents": [UserIntent.ANSWER_CURRENT_QUESTION.value],
        }
        content = {
            "independent_variable": "两个电荷之间的距离和相对方向",
            "observations": "中间区域的场线弯曲与连接",
            "controlled_conditions": "电荷量、物体尺寸和显示精度",
        }
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.ANSWER_CURRENT_QUESTION,
                target=Stage.VARIABLES_AND_CONDITIONS.value,
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "ANSWER_PENDING_QUESTION",
                        "target": Stage.VARIABLES_AND_CONDITIONS.value,
                        "operation": "REPLACE",
                        "content": content,
                        "confidence": 0.98,
                    }
                ],
                actions_authoritative=True,
            ),
            pending,
        )
        apply_resolved_intent(session, resolved, pending, str(content))

        snapshot = stage_design_state_snapshot(session)
        self.assertEqual(snapshot["independent_variable"], content["independent_variable"])
        self.assertEqual(snapshot["observations"], content["observations"])
        self.assertEqual(
            snapshot["controlled_conditions"],
            content["controlled_conditions"],
        )

    def test_cross_field_revision_resolves_completed_stage_pending_action(self) -> None:
        session = variable_stage_session("design_variable_revision_resolves_pending")
        apply_stage_field_updates(
            session,
            [
                {
                    "field": "observations",
                    "operation": "REPLACE",
                    "value": "中间区域的场线弯曲程度",
                },
                {
                    "field": "controlled_conditions",
                    "operation": "REPLACE",
                    "value": "电荷量、显示比例和观察平面",
                },
            ],
            stage=Stage.VARIABLES_AND_CONDITIONS,
            provenance="STUDENT_CONFIRMED",
        )
        pending = save_pending_action(
            session,
            Stage.VARIABLES_AND_CONDITIONS,
            StepOutput(
                assistant_message="变量框架已经有了，我们只需要调整主动改变的量。",
                stage_payload={},
                student_task="你想怎样调整自变量？",
            ),
        )
        replacement = "两个电荷之间的距离，并把相对方向作为补充变化"
        resolved = validate_resolved_intent(
            resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                target="independent_variable",
                confidence=0.98,
                source="SEMANTIC_MODEL",
                dialogue_acts=[
                    {
                        "type": "MODIFY_STAGE_FIELD",
                        "target": "independent_variable",
                        "operation": "REPLACE",
                        "content": replacement,
                        "confidence": 0.98,
                    }
                ],
                actions_authoritative=True,
            ),
            pending,
        )

        apply_resolved_intent(session, resolved, pending, replacement)

        self.assertEqual(
            stage_design_state_snapshot(session)["independent_variable"],
            replacement,
        )
        self.assertIsNone(current_pending_action(session))
        self.assertEqual(
            resolved["semantic_updates"]["pending_answer_status"],
            "CLEAR",
        )

    def test_emvr_field_answer_closes_matching_open_question_across_all_fields_and_stages(self) -> None:
        """Every EMVR field path consumes its matching prompt in every stage family."""

        stages = list(Stage)
        for field_index, field in enumerate(sorted(EMVR_EDITABLE_FIELDS)):
            stage_index = field_index % len(stages)
            stage = stages[stage_index]
            with self.subTest(stage=stage.value, field=field):
                session = DesignSession(
                    design_id=f"emvr_pending_{stage.value}_{field}",
                    interaction_state=InteractionState.EMVR_DIRECT,
                    current_stage_index=stage_index,
                    design_context={"emvr_design": {}},
                )
                pending = save_pending_action(
                    session,
                    stage,
                    StepOutput(
                        assistant_message="请说明这个阶段需要的对象。",
                        stage_payload={
                            "pending_action": {
                                "type": "ANSWER_EMVR_STAGE_QUESTION",
                                "interaction_state": InteractionState.EMVR_DIRECT.value,
                                "subject": field,
                                "answer_fields": [field],
                            }
                        },
                        student_task="请补充当前EMVR设计项。",
                    ),
                )
                answer = f"{field}的已确认设计内容"
                resolved = validate_resolved_intent(
                    resolved_intent(
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                        target=field,
                        confidence=0.99,
                        source="SEMANTIC_EMVR_FIELD_RECOVERY",
                        dialogue_acts=[
                            {
                                "type": "MODIFY_EMVR_FIELD",
                                "target": field,
                                "operation": "REPLACE",
                                "content": answer,
                                "confidence": 0.99,
                            }
                        ],
                        actions_authoritative=True,
                    ),
                    pending,
                )

                apply_resolved_intent(session, resolved, pending, answer)

                self.assertIsNone(current_pending_action(session))
                self.assertEqual(
                    resolved["semantic_updates"]["pending_answer_status"],
                    "CLEAR",
                )

    def test_clarification_language_matches_interaction_mode(self) -> None:
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": "variable_plan",
            "question": "哪些参数需要保持不变？",
            "allowed_intents": [UserIntent.ANSWER_CURRENT_QUESTION.value],
        }

        guided = clarification_output(
            pending,
            InteractionState.GUIDED_DESIGN,
        )
        emvr = clarification_output(
            pending,
            InteractionState.EMVR_DIRECT,
        )

        self.assertIn("我们还差一个关键点", guided.assistant_message)
        self.assertIn("可修改的参考", guided.assistant_message)
        self.assertIn("当前设计评审", emvr.assistant_message)
        self.assertIn("专业草稿", emvr.assistant_message)

    def test_emvr_field_roles_follow_canonical_dialogue_acts(self) -> None:
        session = DesignSession(
            design_id="emvr_canonical_role_isolation",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        acts = [
            {
                "type": "MODIFY_DESIGN_FIELD",
                "target": "research_object",
                "operation": "REPLACE",
                "content": "两个带电物体",
                "semantic_key": "two_charged_objects",
                "confidence": 0.99,
            },
            {
                "type": "MODIFY_DESIGN_FIELD",
                "target": "course_relationship",
                "operation": "REPLACE",
                "content": "库仑定律与叠加原理",
                "semantic_key": "coulomb_and_superposition",
                "confidence": 0.99,
            },
            {
                "type": "MODIFY_STAGE_FIELD",
                "target": "interactions",
                "operation": "REPLACE",
                "content": "使用手柄拖拽两个带电物体",
                "semantic_key": "controller_drag_charged_objects",
                "confidence": 0.99,
            },
            {
                "type": "MODIFY_STAGE_FIELD",
                "target": "independent_variable",
                "operation": "REPLACE",
                "content": ["两个物体之间的距离", "两个物体的相对方向"],
                "semantic_key": "charge_separation_and_orientation",
                "confidence": 0.99,
            },
            {
                "type": "MODIFY_STAGE_FIELD",
                "target": "observations",
                "operation": "REPLACE",
                "content": "靠近过程中场线的合并、扭曲和重排",
                "semantic_key": "field_line_reconfiguration",
                "confidence": 0.99,
            },
        ]
        generator = MultiActSemanticGenerator(
            acts,
            semantic_updates={
                "emvr_design_update": {
                    # Deliberately misclassified parallel model output.  The
                    # state machine must project the canonical acts instead.
                    "required_behaviors": ["库仑定律与叠加原理共同决定场线"],
                    "changed_quantities": ["场线的合并、扭曲和重排"],
                    "observed_quantities": ["两个物体之间的距离"],
                    "field_updates": [
                        {
                            "field_id": "required_behaviors",
                            "operation": "REPLACE",
                            "value": ["库仑定律与叠加原理共同决定场线"],
                        },
                        {
                            "field_id": "changed_quantities",
                            "operation": "REPLACE",
                            "value": ["场线的合并、扭曲和重排"],
                        },
                    ],
                }
            },
        )
        engine = WorkflowEngine(generator=generator)
        engine.store.save(session)
        engine.process_turn(
            session.design_id,
            {"message": "说明研究对象、课程关系、交互、自变量和观察现象。"},
        )

        stored = engine.store.get(session.design_id)
        merged = merge_emvr_structured_requirements(
            stored.design_context["emvr_design"]
        )
        self.assertEqual(merged["course_relationship"], "库仑定律与叠加原理")
        self.assertEqual(
            merged["required_behaviors"], ["使用手柄拖拽两个带电物体"]
        )
        self.assertEqual(
            merged["changed_quantities"],
            ["两个物体之间的距离", "两个物体的相对方向"],
        )
        self.assertEqual(
            merged["observed_quantities"],
            ["靠近过程中场线的合并、扭曲和重排"],
        )

    def test_emvr_comparison_edit_cannot_fill_learning_objective(self) -> None:
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_COMPARISON",
                    "target": "charge_polarity_cases",
                    "operation": "REPLACE",
                    "content": {
                        "action": "CREATE",
                        "title": "电荷极性关系",
                        "new_cases": ["同种电荷", "异种电荷"],
                        "semantic_key": "charge_polarity_relation_cases",
                        "case_semantic_keys": {
                            "同种电荷": "same_sign_charges",
                            "异种电荷": "opposite_sign_charges",
                        },
                    },
                    "semantic_key": "charge_polarity_relation_cases",
                    "confidence": 0.99,
                }
            ],
            semantic_updates={
                "emvr_design_update": {
                    # A parallel model snapshot may be stale or misclassified;
                    # the post-commit canonical comparison must win.
                    "comparison_cases": ["无损线路", "有损线路"],
                    "field_updates": [
                        {
                            "field_id": "comparison_cases",
                            "operation": "REPLACE",
                            "value": ["无损线路", "有损线路"],
                        }
                    ],
                }
            },
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="emvr_comparison_not_objective",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.LEARNING_OBJECTIVES),
        )
        save_pending_action(
            session,
            Stage.LEARNING_OBJECTIVES,
            StepOutput(
                assistant_message="请核对学习目标草稿。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "interaction_state": InteractionState.EMVR_DIRECT.value,
                        "subject": Stage.LEARNING_OBJECTIVES.value,
                        "proposal": {"learning_objective": "理解场线变化"},
                    }
                },
                student_task="需要修改时直接指出对应内容。",
            ),
        )
        engine.store.save(session)

        engine.process_turn(
            session.design_id,
            {"message": "修改基础比较为“同种电荷与异种电荷”。"},
        )

        stored = engine.store.get(session.design_id)
        snapshot = design_state_snapshot(stored)
        self.assertEqual(snapshot["learning_objective"], "")
        self.assertEqual(
            snapshot["baseline_comparisons"][0]["cases"],
            ["同种电荷", "异种电荷"],
        )
        merged = merge_emvr_structured_requirements(
            stored.design_context["emvr_design"]
        )
        self.assertEqual(
            merged["comparison_cases"],
            ["同种电荷", "异种电荷"],
        )

    def test_guided_cross_field_edits_do_not_bind_to_visible_pending_item(self) -> None:
        question = "距离改变时，同种与异种电荷之间的场线形态有何差异？"
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": question,
                    "semantic_key": "distance_polarity_field_line_question",
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_COMPARISON",
                    "target": "charge_polarity_cases",
                    "operation": "REPLACE",
                    "content": {
                        "action": "CREATE",
                        "new_cases": ["同种电荷", "异种电荷"],
                        "semantic_key": "charge_polarity_relation_cases",
                    },
                    "semantic_key": "charge_polarity_relation_cases",
                    "confidence": 0.99,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="guided_cross_field_pending_isolation",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.LEARNING_OBJECTIVES),
        )
        save_pending_action(
            session,
            Stage.LEARNING_OBJECTIVES,
            StepOutput(
                assistant_message="我们来完善学习目标。",
                stage_payload={
                    "pending_action": {
                        "type": "ANSWER_STAGE_QUESTION",
                        "subject": Stage.LEARNING_OBJECTIVES.value,
                        "question": "你希望通过实验学会解释什么？",
                    }
                },
                student_task="先说说你的学习目标。",
            ),
        )
        message = (
            f"先把研究问题改为“{question}”，基础比较改为同种电荷与异种电荷；"
            "学习目标我稍后再补。"
        )
        pending = current_pending_action(session)
        raw = generator.resolve_intent(session, message, pending, {})
        validated = validate_resolved_intent(raw, pending)
        apply_resolved_intent(session, validated, pending, message)

        snapshot = design_state_snapshot(session)
        self.assertEqual(snapshot["research_question"], question)
        self.assertEqual(snapshot["learning_objective"], "")
        self.assertEqual(
            snapshot["baseline_comparisons"][0]["cases"],
            ["同种电荷", "异种电荷"],
        )

    def test_emvr_multiple_revisions_commit_every_named_field(self) -> None:
        question = (
            "两个带电物体的距离从远到近变化时，同种与异种电荷配置下的场线"
            "合并、扭曲和重排有何差异？"
        )
        generator = MultiActSemanticGenerator(
            [
                {
                    "type": "MODIFY_DESIGN_FIELD",
                    "target": "research_question",
                    "operation": "REPLACE",
                    "content": question,
                    "semantic_key": "distance_polarity_field_line_causal_question",
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "independent_variable",
                    "operation": "MERGE",
                    "content": "两个带电物体之间的距离",
                    "semantic_key": "charge_separation_distance",
                    "confidence": 0.99,
                },
                {
                    "type": "MODIFY_STAGE_FIELD",
                    "target": "independent_variable",
                    "operation": "MERGE",
                    "content": "两个带电物体的相对方向",
                    "semantic_key": "relative_charge_orientation",
                    "confidence": 0.99,
                },
            ]
        )
        engine = WorkflowEngine(generator=generator)
        session = DesignSession(
            design_id="emvr_multi_revision_all_fields",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.RESEARCH_QUESTION),
        )
        save_pending_action(
            session,
            Stage.RESEARCH_QUESTION,
            StepOutput(
                assistant_message="请核对研究问题草稿。",
                stage_payload={
                    "pending_action": {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "interaction_state": InteractionState.EMVR_DIRECT.value,
                        "subject": Stage.RESEARCH_QUESTION.value,
                        "proposal": {"research_question": "原研究问题"},
                    }
                },
                student_task="可以一次提出多项修改。",
            ),
        )
        engine.store.save(session)

        engine.process_turn(
            session.design_id,
            {
                "message": (
                    f"把研究问题改为“{question}”；同时把距离和相对方向都加入VR中可调内容。"
                )
            },
        )

        stored = engine.store.get(session.design_id)
        self.assertEqual(design_state_snapshot(stored)["research_question"], question)
        variables = stage_design_state_snapshot(stored)["independent_variable"]
        self.assertIn("两个带电物体之间的距离", variables)
        self.assertIn("两个带电物体的相对方向", variables)
        merged = merge_emvr_structured_requirements(
            stored.design_context["emvr_design"]
        )
        self.assertEqual(merged["research_question"], question)
        self.assertIn("两个带电物体之间的距离", merged["changed_quantities"])
        self.assertIn("两个带电物体的相对方向", merged["changed_quantities"])

    def test_emvr_objective_revision_updates_only_the_named_objective(self) -> None:
        emvr_design = {
            "field_state": {
                "conceptual_objective": "理解库仑定律与叠加原理",
                "calculation_objective": "计算两个点电荷的合场强",
                "analysis_objective": "比较同种与异种电荷的场线差异",
                "vr_interaction_objective": "通过拖拽改变两个电荷的间距",
                "observation_objective": "观察中间区域场线的弯曲与重排",
                "learning_objectives": [
                    "理解库仑定律与叠加原理",
                    "计算两个点电荷的合场强",
                    "比较同种与异种电荷的场线差异",
                    "通过拖拽改变两个电荷的间距",
                    "观察中间区域场线的弯曲与重排",
                ],
            }
        }

        apply_emvr_field_updates(
            emvr_design,
            normalize_emvr_design_update(
                {
                    "field_updates": [
                        {
                            "field_id": "calculation_objective",
                            "operation": "REPLACE",
                            "value": "计算不同间距下中点场强的大小和方向",
                        }
                    ]
                }
            ),
        )

        merged = merge_emvr_structured_requirements(emvr_design)
        self.assertEqual(
            merged["calculation_objective"],
            "计算不同间距下中点场强的大小和方向",
        )
        self.assertEqual(
            merged["conceptual_objective"], "理解库仑定律与叠加原理"
        )
        self.assertEqual(
            merged["vr_interaction_objective"],
            "通过拖拽改变两个电荷的间距",
        )
        self.assertEqual(len(merged["learning_objectives"]), 5)
        self.assertIn(
            "计算不同间距下中点场强的大小和方向",
            merged["learning_objectives"],
        )
        self.assertNotIn(
            "计算两个点电荷的合场强",
            merged["learning_objectives"],
        )

    def test_emvr_theory_relation_without_committed_field_binding_is_discarded(self) -> None:
        emvr_design = {
            "field_state": {
                "research_question": "比较两个场源靠近时的场线变化",
                "observed_quantities": ["空间场线分布"],
            }
        }
        update = normalize_emvr_design_update(
            {
                "theory_links": [
                    {
                        "relation_id": "CHARGED_PARTICLE_FORCE",
                        "supports_design_content": "属于相邻的电磁学知识",
                        "supports_design_fields": ["object_constraints"],
                    }
                ]
            }
        )

        apply_emvr_field_updates(emvr_design, update)

        merged = merge_emvr_structured_requirements(emvr_design)
        self.assertNotIn("theory_links", merged)
        self.assertNotIn("theory_relation_ids", merged)

    def test_emvr_theory_binding_is_invalidated_when_supported_design_changes(self) -> None:
        emvr_design = {
            "field_state": {
                "research_question": "改变两个场源的距离时，空间场分布如何变化？",
                "observed_quantities": ["空间场分布"],
            }
        }
        theory = normalize_emvr_design_update(
            {
                "theory_links": [
                    {
                        "relation_id": "FIELD_SUPERPOSITION",
                        "supports_design_content": "解释两个场源共同产生的空间场",
                        "supports_design_fields": [
                            "research_question",
                            "observed_quantities",
                        ],
                    }
                ]
            }
        )
        apply_emvr_field_updates(emvr_design, theory)
        self.assertIn(
            "FIELD_SUPERPOSITION",
            merge_emvr_structured_requirements(emvr_design)["theory_relation_ids"],
        )

        apply_emvr_field_updates(
            emvr_design,
            normalize_emvr_design_update(
                {
                    "field_updates": [
                        {
                            "field_id": "research_question",
                            "operation": "REPLACE",
                            "value": "改变材料损耗时，传播衰减如何变化？",
                        }
                    ]
                }
            ),
        )

        merged = merge_emvr_structured_requirements(emvr_design)
        self.assertNotIn("theory_links", merged)
        self.assertNotIn("theory_relation_ids", merged)
        self.assertEqual(emvr_design.get("theory_link_state"), {})

    def test_emvr_precise_fields_replace_a_vague_authoritative_brief(self) -> None:
        emvr_design = {
            "field_state": {"experiment_brief": "我想做一个静电场实验"},
            "experiment_brief": "我想做一个静电场实验",
            "current_brief": "我想做一个静电场实验",
        }
        apply_emvr_field_updates(
            emvr_design,
            normalize_emvr_design_update(
                {
                    "field_updates": [
                        {
                            "field_id": "research_object",
                            "operation": "REPLACE",
                            "value": "两个点电荷",
                        },
                        {
                            "field_id": "required_behaviors",
                            "operation": "REPLACE",
                            "value": ["用手柄拖拽两个点电荷"],
                        },
                        {
                            "field_id": "changed_quantities",
                            "operation": "REPLACE",
                            "value": ["电荷间距", "电荷类型"],
                        },
                        {
                            "field_id": "observed_quantities",
                            "operation": "REPLACE",
                            "value": ["场线的合并、扭曲和重排"],
                        },
                    ]
                }
            ),
        )

        merged = merge_emvr_structured_requirements(emvr_design)
        brief = merged["experiment_brief"]
        self.assertIn("两个点电荷", brief)
        self.assertIn("手柄拖拽", brief)
        self.assertIn("电荷间距", brief)
        self.assertIn("场线的合并、扭曲和重排", brief)
        self.assertNotEqual(brief, "我想做一个静电场实验")
        self.assertEqual(emvr_design["brief_source"], "STRUCTURED_FIELD_SYNTHESIS")

    def test_emvr_rule_fallback_does_not_commit_unbound_course_theory(self) -> None:
        session = DesignSession(
            design_id="emvr_no_unconfirmed_theory",
            interaction_state=InteractionState.EMVR_DIRECT,
            current_stage_index=list(Stage).index(Stage.THEORETICAL_FRAMEWORK),
            design_context={
                "emvr_design": {
                    "field_state": {
                        "research_question": "两个点电荷靠近时场线如何重新分布？",
                        "changed_quantities": ["两个点电荷的间距"],
                        "observed_quantities": ["场线形态"],
                    }
                }
            },
        )

        output = RuleBasedStageGenerator().generate(session, "")

        formula_ids = {
            item["id"] for item in output.stage_payload["core_equations"]
        }
        self.assertEqual(formula_ids, set())
        self.assertEqual(output.stage_payload["formula_support_map"], [])
        self.assertEqual(
            output.stage_payload["theory_selection_status"],
            "needs_semantic_theory_confirmation",
        )

    def test_cross_field_revision_isolated_from_pending_subject_in_every_stage(self) -> None:
        """A visible stage prompt must never become an implicit write target."""

        revised_question = "距离变化时，同种与异种电荷的场线分布有何差异？"
        acts = [
            {
                "type": "MODIFY_DESIGN_FIELD",
                "target": "research_question",
                "operation": "REPLACE",
                "content": revised_question,
                "semantic_key": "distance_charge_type_field_question",
                "confidence": 0.99,
            },
            {
                "type": "MODIFY_COMPARISON",
                "target": "charge_type_cases",
                "operation": "REPLACE",
                "content": {
                    "action": "CREATE",
                    "title": "电荷类型",
                    "new_cases": ["同种电荷", "异种电荷"],
                    "semantic_key": "charge_type_cases",
                },
                "semantic_key": "charge_type_cases",
                "confidence": 0.99,
            },
        ]
        for interaction_state in (
            InteractionState.GUIDED_DESIGN,
            InteractionState.EMVR_DIRECT,
        ):
            for stage_index, stage in enumerate(Stage):
                with self.subTest(mode=interaction_state.value, stage=stage.value):
                    session = DesignSession(
                        design_id=(
                            f"cross_field_{interaction_state.value}_{stage.value}"
                        ),
                        interaction_state=interaction_state,
                        current_stage_index=stage_index,
                    )
                    apply_design_updates(
                        session,
                        [
                            {
                                "field": "learning_objective",
                                "operation": "REPLACE",
                                "value": "原学习目标保持不变",
                            }
                        ],
                        provenance="TEST_SETUP",
                    )
                    pending = {
                        "type": "CONFIRM_STAGE_OR_MODIFY",
                        "subject": stage.value,
                        "proposal": {"stage": stage.value},
                        "interaction_state": interaction_state.value,
                        "allowed_intents": [
                            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
                            UserIntent.MODIFY_PREVIOUS_PROPOSAL.value,
                            UserIntent.UNCLEAR.value,
                        ],
                    }
                    raw = resolved_intent(
                        UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                        target="research_question",
                        confidence=0.99,
                        source="SEMANTIC_TEST",
                        dialogue_acts=acts,
                        actions_authoritative=True,
                        semantic_updates={
                            "emvr_design_update": {
                                "learning_objectives": [
                                    "错误地把整条修改写进当前阶段"
                                ],
                                "required_behaviors": ["库仑定律与叠加原理"],
                                "comparison_cases": ["错误案例"],
                            }
                        },
                    )
                    validated = validate_resolved_intent(raw, pending)
                    apply_resolved_intent(
                        session,
                        validated,
                        pending,
                        "修改研究问题，并把基础比较改为同种电荷与异种电荷",
                    )

                    snapshot = design_state_snapshot(session)
                    self.assertEqual(
                        snapshot["learning_objective"], "原学习目标保持不变"
                    )
                    self.assertEqual(snapshot["research_question"], revised_question)
                    self.assertEqual(
                        snapshot["baseline_comparisons"][0]["cases"],
                        ["同种电荷", "异种电荷"],
                    )
                    self.assertEqual(session.current_stage, stage)
                    emvr_update = validated["semantic_updates"].get(
                        "emvr_design_update"
                    )
                    if interaction_state is InteractionState.EMVR_DIRECT:
                        self.assertFalse(
                            emvr_update
                            and emvr_update.get("learning_objectives")
                        )
                        self.assertFalse(
                            emvr_update
                            and emvr_update.get("required_behaviors")
                        )
                        self.assertFalse(
                            emvr_update
                            and emvr_update.get("comparison_cases")
                        )


if __name__ == "__main__":
    unittest.main()
