from __future__ import annotations

import unittest

from ece329_workflow.dialogue_state import (
    UserIntent,
    apply_resolved_intent,
    build_carried_context,
    current_pending_action,
    deterministic_intent,
    hydrate_pending_action_from_history,
    resolved_intent,
    save_pending_action,
    validate_resolved_intent,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.generator import guided_stage_entry_output
from ece329_workflow.idea_development import (
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
    ) -> None:
        self.intent = intent
        self.confidence = confidence
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
            self.intent,
            target=str(pending_action.get("subject") or "") if pending_action else None,
            resolved_value=(pending_action.get("proposal") if pending_action else None),
            preserve_current_design=True,
            confidence=self.confidence,
            source="SEMANTIC_TEST",
            semantic_updates=self.semantic_updates,
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
        confirmed = deterministic_intent("确认", open_question)
        assert confirmed is not None
        self.assertEqual(
            confirmed["intent"],
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
        )

        proposal_confirmation = {
            "type": "CONFIRM_OR_MODIFY",
            "subject": "experiment_idea_outline",
            "proposal": {"complete": True},
            "allowed_intents": [UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value],
        }
        accepted = deterministic_intent("保留", proposal_confirmation)
        assert accepted is not None
        self.assertEqual(
            accepted["intent"],
            UserIntent.ACCEPT_PREVIOUS_PROPOSAL.value,
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
                                UserIntent.ANSWER_CURRENT_QUESTION,
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
                                UserIntent.ANSWER_CURRENT_QUESTION,
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
                candidate = f"这是学生为{stage.value}提供的具体说明"

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
                self.assertEqual(omitted["intent"], UserIntent.UNCLEAR.value)

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
                        stage_payload={"independent_variable": "两个源之间的距离"},
                        student_task="还需要保持哪些条件不变？",
                    )
                return StepOutput(
                    assistant_message="控制条件也已补充。",
                    stage_payload={"controlled_variables": ["电荷量", "观察方式"]},
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
        generator = ScriptedSemanticGenerator(UserIntent.ANSWER_CURRENT_QUESTION)
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
                        UserIntent.ANSWER_CURRENT_QUESTION,
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

    def test_missing_facet_decision_clarifies_instead_of_blaming_student_answer(self) -> None:
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

        self.assertTrue(result["stage_payload"]["clarification_required"])
        self.assertIn("还没有准确判断", result["assistant_message"])
        self.assertNotIn("研究问题还需要同时出现", result["assistant_message"])
        stored = engine.get_design(session.design_id)["design_context"][
            "idea_development"
        ]
        self.assertEqual(stored["active_facet_id"], "research_question")

    def test_repeated_semantic_omission_does_not_repeat_same_clarification(self) -> None:
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
        second = engine.process_turn(
            session.design_id,
            {"message": "上一句说的就是我想比较和观察的内容"},
        )

        self.assertNotEqual(first["assistant_message"], second["assistant_message"])
        self.assertIn("不需要再次重写整段", second["assistant_message"])
        pending = current_pending_action(engine.store.get(session.design_id))
        assert pending is not None
        self.assertEqual(pending["subject"], "research_question")

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

        self.assertIn("只确认“研究问题”", first["assistant_message"])
        self.assertIn("不再让你重写整段“研究问题”", second["assistant_message"])
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

    def test_semantic_facet_updates_replace_keyword_facet_detection(self) -> None:
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
        self.assertEqual(development["facets"]["learning_objective"]["status"], "MISSING")

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


if __name__ == "__main__":
    unittest.main()
