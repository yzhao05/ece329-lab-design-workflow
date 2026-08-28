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
    record_pending_clarification,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.generator import guided_stage_entry_output
from ece329_workflow.emvr_design import (
    apply_emvr_field_updates,
    merge_emvr_structured_requirements,
    normalize_emvr_design_update,
)
from ece329_workflow.design_state import (
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
                        "action": "MODIFY",
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

        emvr_design: dict = {}
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
        self.assertIn("同种电荷、异种电荷", output.assistant_message)

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

    def test_stage_level_confirmation_advances_without_micro_confirmation_loop(self) -> None:
        class CompletionGenerator(ScriptedSemanticGenerator):
            def __init__(self) -> None:
                super().__init__(
                    UserIntent.ANSWER_CURRENT_QUESTION,
                    semantic_updates={"pending_answer_status": "CLEAR"},
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

        ready = engine.process_turn(
            session.design_id,
            {"message": "拖拽改变两个源的距离，观察电场线和通量"},
        )
        self.assertTrue(ready["stage_payload"]["stage_ready_for_confirmation"])
        self.assertIn("不用再逐项确认", ready["assistant_message"])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["type"], "CONFIRM_STAGE_OR_MODIFY")
        self.assertTrue(pending["advance_on_accept"])

        advanced = engine.process_turn(session.design_id, {"message": "确认"})
        self.assertEqual(
            advanced["transitioned_from_stage"],
            Stage.VARIABLES_AND_CONDITIONS.value,
        )
        self.assertEqual(advanced["current_stage"], Stage.CONCEPTUAL_PROCEDURE.value)

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

        self.assertEqual(guided["intent"], UserIntent.ANSWER_CURRENT_QUESTION.value)
        self.assertEqual(emvr["intent"], UserIntent.MODIFY_PREVIOUS_PROPOSAL.value)

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
                            UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                            target=facet_id,
                            confidence=0.98,
                            source="SEMANTIC_TEST",
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
                            UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                            target=stage.value,
                            confidence=0.98,
                            source="SEMANTIC_TEST",
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
        self.assertIn(interaction, emvr_design["current_brief"])
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
                    UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                    target="research_question",
                    confidence=0.98,
                    source="SEMANTIC_TEST",
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
            "subject": Stage.IDEA_BRAINSTORMING.value,
            "candidate_answer": (
                "在VR中拖动两个带电物体改变距离，观察导体与介质附近的电场线变化"
            ),
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

        # If the next semantic result confirms that the saved turn was a
        # modification but omits its value, recover the saved content.
        recovered = validate_resolved_intent(
            resolved_intent(
                UserIntent.MODIFY_PREVIOUS_PROPOSAL,
                resolved_value=None,
                confidence=0.98,
                source="SEMANTIC_TEST",
            ),
            stored,
        )
        self.assertEqual(recovered["resolved_value"], supplement)
        self.assertEqual(recovered["source"], "CONFIRMED_PENDING_MODIFICATION")

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


if __name__ == "__main__":
    unittest.main()
