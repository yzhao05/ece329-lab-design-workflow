from __future__ import annotations

import json
import unittest
from copy import deepcopy
from typing import Any

from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator, build_exploration_scenes
from ece329_workflow.guardrails import build_stage_one_turn_context
from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.openai_generator import (
    FallbackStageGenerator,
    ModelOutputError,
    ModelHTTPError,
    ModelServiceError,
    OpenAIStageGenerator,
    generator_from_environment,
)
from ece329_workflow.prompts import build_prompt_packet


class FakeTransport:
    def __init__(
        self,
        output: dict[str, Any] | None = None,
        error: Exception | None = None,
        response_id: str = "resp_test",
        errors: list[Exception] | None = None,
        outputs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.output = output
        self.outputs = list(outputs or [])
        self.error = error
        self.response_id = response_id
        self.errors = list(errors or [])
        self.requests: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(deepcopy(payload))
        if self.errors:
            raise self.errors.pop(0)
        if self.error:
            raise self.error
        selected_output = self.outputs.pop(0) if self.outputs else self.output
        return {
            "id": self.response_id,
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(selected_output, ensure_ascii=False),
                        }
                    ],
                }
            ]
        }


def guided_session(stage_index: int = 0) -> DesignSession:
    return DesignSession(
        design_id="design_test",
        interaction_state=InteractionState.GUIDED_DESIGN,
        current_stage_index=stage_index,
        design_context={"idea": {"original": "研究传输线驻波"}},
    )


def valid_output(**overrides: Any) -> dict[str, Any]:
    brainstorm = KNOWLEDGE.brainstorm_options(
        "研究传输线驻波",
        limit=3,
        seed_key="design_test:0",
    )
    scenes = build_exploration_scenes(brainstorm)
    output: dict[str, Any] = {
        "assistant_message": (
            "我们先用一幅可改造、可组合的物理图景比较ECE329课上所学概念"
            "之间的关系。启发性延伸只用于打开思路。"
        ),
        "stage_payload_json": json.dumps(
            {
                "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                "input_category": "COURSE_CONTENT",
                "alternative_ideas": brainstorm,
                "exploration_scenes": scenes,
            },
            ensure_ascii=False,
        ),
        "student_task": "你更想探索哪一种变化关系？",
        "visualization_json": None,
        "assumptions": [],
        "warnings": [],
    }
    output.update(overrides)
    return output


def retrieved_brainstorm_options(
    message: str,
    session: DesignSession | None = None,
) -> list[dict[str, Any]]:
    packet = build_prompt_packet(session or guided_session(), message)
    return packet["context"]["knowledge_retrieval"]["brainstorm_options"]


class OpenAIStageGeneratorTests(unittest.TestCase):
    def test_context_intent_resolver_uses_a_separate_structured_request(self) -> None:
        transport = FakeTransport(
            {
                "intent": "ADVANCE_STAGE",
                "target": "variable_plan",
                "resolved_value_json": None,
                "semantic_updates_json": json.dumps(
                    {
                        "no_direction": False,
                        "selected_option_ids": [],
                        "facet_updates": [],
                        "comparison_updates": [],
                    },
                    ensure_ascii=False,
                ),
                "advance_requested": True,
                "preserve_current_design": True,
                "confidence": 0.97,
            }
        )
        generator = OpenAIStageGenerator(transport=transport, model="test-model")
        session = guided_session(stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS))
        pending = {
            "type": "CONFIRM_OR_MODIFY",
            "subject": "variable_plan",
            "proposal": ["距离", "电场线形状"],
            "question": "是否保留当前变量安排？",
            "allowed_intents": ["ADVANCE_STAGE", "ACCEPT_PREVIOUS_PROPOSAL"],
        }

        result = generator.resolve_intent(
            session,
            "沿用刚才的安排，继续往下整理",
            pending,
            {"independent_variable": ["距离"]},
        )

        self.assertEqual(result["intent"], "ADVANCE_STAGE")
        self.assertFalse(result["semantic_updates"]["no_direction"])
        request = transport.requests[0]
        self.assertEqual(request["text"]["format"]["name"], "ece329_context_intent")
        self.assertFalse(request["store"])
        self.assertNotIn("previous_response_id", request)
        serialized = request["input"][0]["content"][0]["text"]
        self.assertIn("pending_action", serialized)
        self.assertIn("沿用刚才的安排", serialized)
        self.assertIn("no_direction表示学生当前没有可供继续完善的实验方向", request["instructions"])
        self.assertIn("不得判成课外主题", request["instructions"])
        self.assertIn("给出一个可能、参考、示例或你的判断", request["instructions"])
        self.assertIn("只返回实质设计内容", request["instructions"])
        self.assertIn("CONFIRM_STAGE_OR_MODIFY", request["instructions"])

    def test_intent_resolver_repairs_an_omitted_active_facet_decision(self) -> None:
        base = {
            "intent": "ANSWER_CURRENT_QUESTION",
            "target": "research_question",
            "resolved_value_json": None,
            "advance_requested": False,
            "preserve_current_design": True,
            "confidence": 0.96,
        }
        transport = FakeTransport(
            outputs=[
                {
                    **base,
                    "semantic_updates_json": json.dumps(
                        {
                            "facet_updates": [],
                            "comparison_updates": [
                                {
                                    "comparison_id": "polarity_cases",
                                    "action": "ACCEPT",
                                    "cases": ["同种电荷", "异种电荷"],
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    **base,
                    "semantic_updates_json": json.dumps(
                        {
                            "facet_updates": [
                                {
                                    "facet_id": "research_question",
                                    "status": "CLEAR",
                                }
                            ],
                            "comparison_updates": [
                                {
                                    "comparison_id": "polarity_cases",
                                    "action": "ACCEPT",
                                    "cases": ["同种电荷", "异种电荷"],
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        pending = {
            "type": "ANSWER_IDEA_FACET",
            "subject": "research_question",
            "proposal": {"facet_id": "research_question", "title": "研究问题"},
            "question": "你想比较什么条件，并观察哪种现象怎样改变？",
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        generator = OpenAIStageGenerator(transport=transport)

        result = generator.resolve_intent(
            guided_session(),
            "在同种或异种电荷条件下缩短距离，观察场线空间分布的变化",
            pending,
            {
                "idea_development": {
                    "active_facet_id": "research_question",
                },
                "baseline_comparisons": [
                    {
                        "comparison_id": "polarity_cases",
                        "recommended_cases": ["同种电荷", "异种电荷"],
                    }
                ],
            },
        )

        self.assertEqual(result["intent"], "ANSWER_CURRENT_QUESTION")
        self.assertEqual(
            result["semantic_updates"]["facet_updates"],
            [{"facet_id": "research_question", "status": "CLEAR"}],
        )
        self.assertEqual(len(transport.requests), 2)
        repair_text = transport.requests[1]["input"][0]["content"][-1]["text"]
        self.assertIn("research_question", repair_text)
        self.assertIn("同时保留comparison_updates", repair_text)
        instructions = transport.requests[0]["instructions"]
        self.assertIn("research_question不要求使用问号或疑问句", instructions)
        self.assertIn("candidate_answer", instructions)

    def test_intent_resolver_repairs_omitted_later_stage_answer_status(self) -> None:
        base = {
            "intent": "ANSWER_CURRENT_QUESTION",
            "target": "VARIABLES_AND_CONDITIONS",
            "resolved_value_json": None,
            "advance_requested": False,
            "preserve_current_design": True,
            "confidence": 0.96,
        }
        transport = FakeTransport(
            outputs=[
                {**base, "semantic_updates_json": None},
                {
                    **base,
                    "semantic_updates_json": json.dumps(
                        {"pending_answer_status": "CLEAR"},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        pending = {
            "type": "ANSWER_STAGE_QUESTION",
            "subject": "VARIABLES_AND_CONDITIONS",
            "proposal": {"stage_title": "变量与条件"},
            "question": "哪些量主动改变、观察或保持不变？",
            "allowed_intents": ["ANSWER_CURRENT_QUESTION", "UNCLEAR"],
        }
        generator = OpenAIStageGenerator(transport=transport)

        result = generator.resolve_intent(
            guided_session(),
            "改变两个源之间的距离，观察电场线，并保持电荷量不变",
            pending,
            {"research_direction": "比较两个电荷源"},
        )

        self.assertEqual(result["intent"], "ANSWER_CURRENT_QUESTION")
        self.assertEqual(
            result["semantic_updates"]["pending_answer_status"],
            "CLEAR",
        )
        self.assertEqual(len(transport.requests), 2)
        repair_text = transport.requests[1]["input"][0]["content"][-1]["text"]
        self.assertIn("pending_answer_status=CLEAR", repair_text)

    def test_semantic_no_direction_gets_a_friendly_course_brainstorm_lead(self) -> None:
        session = guided_session()
        message = "完全没头绪，先帮我打开思路"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=[],
                idea_context=session.design_context["idea"],
                semantic_updates={"no_direction": True},
            )
        )
        options = retrieved_brainstorm_options(message, session)
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "brainstorm_phase": "BREADTH_EXPLORATION",
                        "alternative_ideas": options,
                        "exploration_scenes": build_exploration_scenes(options),
                    },
                    ensure_ascii=False,
                ),
            )
        )

        output = OpenAIStageGenerator(transport=transport).generate(session, message)

        self.assertTrue(
            output.assistant_message.startswith("好的，那我来帮助你拓展思路")
        )
        self.assertEqual(output.stage_payload["input_category"], "COURSE_CONTENT")
        self.assertNotIn("不属于ECE329", output.assistant_message)

    def test_semantic_no_direction_rejects_a_contradictory_scope_message(self) -> None:
        session = guided_session()
        message = "我现在完全不知道可以研究什么"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=[],
                idea_context=session.design_context["idea"],
                semantic_updates={"no_direction": True},
            )
        )
        options = retrieved_brainstorm_options(message, session)
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "你提出的主题不属于ECE329课程范围。下面是可改造、可组合的物理图景。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "brainstorm_phase": "BREADTH_EXPLORATION",
                        "alternative_ideas": options,
                        "exploration_scenes": build_exploration_scenes(options),
                    },
                    ensure_ascii=False,
                ),
            )
        )

        output = OpenAIStageGenerator(
            transport=transport,
            repair_attempts=0,
        ).generate(session, message)

        self.assertTrue(
            output.assistant_message.startswith("好的，那我来帮助你拓展思路")
        )
        self.assertNotIn("不属于ECE329", output.assistant_message)
        self.assertEqual(output.stage_payload["input_category"], "COURSE_CONTENT")

    def test_generator_builds_responses_request_and_parses_json(self) -> None:
        transport = FakeTransport(valid_output())
        generator = OpenAIStageGenerator(transport=transport, model="test-model")

        output = generator.generate(guided_session(), "研究传输线驻波")

        self.assertEqual(output.stage_payload["brainstorm_activity"], "RELATIONSHIP_DISCOVERY")
        request = transport.requests[0]
        self.assertEqual(request["model"], "test-model")
        self.assertFalse(request["store"])
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertIn("Lecture Notes定义课程范围", request["instructions"])
        self.assertIn("不把Lecture Notes当成唯一参考答案", request["instructions"])
        serialized_input = request["input"][0]["content"][0]["text"]
        self.assertIn('"current_stage": "IDEA_BRAINSTORMING"', serialized_input)

    def test_later_guided_stage_requires_structured_readiness(self) -> None:
        session = guided_session(
            stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS)
        )
        missing = valid_output(
            assistant_message="距离作为主动改变量，场线形状作为观察量。",
            stage_payload_json=json.dumps(
                {"independent_variable": "两个源之间的距离"},
                ensure_ascii=False,
            ),
            student_task="还需要补充哪些控制条件？",
        )
        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(
                transport=FakeTransport(missing),
                repair_attempts=0,
            ).generate(session, "改变距离并观察电场线")

        complete = valid_output(
            assistant_message="距离作为主动改变量，场线形状作为观察量。",
            stage_payload_json=json.dumps(
                {
                    "independent_variable": "两个源之间的距离",
                    "stage_readiness": {
                        "ready_for_confirmation": True,
                        "remaining_gaps": [],
                    },
                },
                ensure_ascii=False,
            ),
            student_task="请整体检查这部分变量安排。",
        )
        output = OpenAIStageGenerator(
            transport=FakeTransport(complete),
            repair_attempts=0,
        ).generate(session, "改变距离并观察电场线")
        self.assertTrue(
            output.stage_payload["stage_readiness"]["ready_for_confirmation"]
        )

    def test_emvr_stage_one_uses_direct_design_contract(self) -> None:
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "original_idea": "在EMVR中探索传输线驻波",
                        "target_phenomenon": "传输线驻波",
                        "possible_vr_interactions": ["调整负载阻抗"],
                        "design_scope": "Unity VR模拟实验设计",
                    },
                    ensure_ascii=False,
                )
            )
        )
        session = guided_session()
        session.interaction_state = InteractionState.EMVR_DIRECT

        output = OpenAIStageGenerator(transport=transport).generate(
            session,
            "请使用EMVR设计传输线驻波实验",
        )

        self.assertEqual(output.stage_payload["target_phenomenon"], "传输线驻波")
        request_text = transport.requests[0]["input"][0]["content"][0]["text"]
        self.assertIn("possible_vr_interactions", request_text)
        self.assertNotIn("alternative_ideas数组", request_text)

    def test_stateful_generator_chains_response_id_and_resends_instructions(self) -> None:
        transport = FakeTransport(valid_output(), response_id="resp_next")
        session = guided_session()
        session.model_context["openai_previous_response_id"] = "resp_previous"
        session.history.append({"user_message": "较早的一轮", "output": {}})
        generator = OpenAIStageGenerator(transport=transport, stateful=True)

        generator.generate(session, "研究传输线驻波")

        request = transport.requests[0]
        self.assertTrue(request["store"])
        self.assertEqual(request["previous_response_id"], "resp_previous")
        self.assertIn("你是ZJUI ECE329实验设计工作流助手", request["instructions"])
        self.assertIn('"recent_history": []', request["input"][0]["content"][0]["text"])
        self.assertEqual(
            session.model_context["openai_previous_response_id"],
            "resp_next",
        )

    def test_stateful_generator_recovers_from_stale_response_chain(self) -> None:
        transport = FakeTransport(
            valid_output(),
            response_id="resp_recovered",
            errors=[ModelHTTPError(404, "previous_response_not_found")],
        )
        session = guided_session()
        session.model_context["openai_previous_response_id"] = "resp_stale"
        generator = OpenAIStageGenerator(transport=transport, stateful=True)

        output = generator.generate(session, "继续研究传输线驻波")

        self.assertEqual(output.stage_payload["input_category"], "COURSE_CONTENT")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(
            transport.requests[0]["previous_response_id"],
            "resp_stale",
        )
        self.assertNotIn("previous_response_id", transport.requests[1])
        self.assertEqual(
            session.model_context["openai_previous_response_id"],
            "resp_recovered",
        )
        self.assertEqual(generator.runtime_info()["response_chain_resets"], 1)

    def test_stateless_generator_uses_local_recent_history_only(self) -> None:
        transport = FakeTransport(valid_output())
        session = guided_session()
        session.model_context["openai_previous_response_id"] = "resp_ignored"
        session.history.append({"user_message": "较早的一轮", "output": {}})

        OpenAIStageGenerator(transport=transport, stateful=False).generate(
            session,
            "研究传输线驻波",
        )

        request = transport.requests[0]
        self.assertFalse(request["store"])
        self.assertNotIn("previous_response_id", request)
        self.assertIn("较早的一轮", request["input"][0]["content"][0]["text"])
        self.assertEqual(
            session.model_context["openai_previous_response_id"],
            "resp_ignored",
        )

    def test_emvr_final_stage_uses_larger_budget_and_requires_package(self) -> None:
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "proposal_status": "complete",
                        "proposal_sections": ["课程联系", "Unity VR设计"],
                        "final_design": {"idea": "传输线驻波"},
                        "builder_pack_handoff": {"purpose": "人工审阅"},
                    },
                    ensure_ascii=False,
                )
            )
        )
        session = guided_session(stage_index=12)
        session.interaction_state = InteractionState.EMVR_DIRECT
        generator = OpenAIStageGenerator(
            transport=transport,
            max_output_tokens=1200,
            final_max_output_tokens=5000,
        )

        generator.generate(session, "生成EMVR最终设计")

        self.assertEqual(transport.requests[0]["max_output_tokens"], 5000)

    def test_guided_stage_one_uses_scene_specific_output_budget(self) -> None:
        transport = FakeTransport(valid_output())
        generator = OpenAIStageGenerator(
            transport=transport,
            max_output_tokens=1200,
            stage_one_max_output_tokens=3600,
        )

        output = generator.generate(guided_session(), "研究传输线驻波")

        self.assertEqual(transport.requests[0]["max_output_tokens"], 3600)
        self.assertTrue(output.stage_payload["exploration_scenes"])
        self.assertEqual(
            output.stage_payload["exploration_scenes"][0]["course_anchor"],
            output.stage_payload["alternative_ideas"][0],
        )

    def test_invalid_model_output_is_rejected(self) -> None:
        transport = FakeTransport({"assistant_message": "missing required fields"})
        generator = OpenAIStageGenerator(transport=transport)

        with self.assertRaises(ModelOutputError):
            generator.generate(guided_session(), "继续")

    def test_unreasonable_request_is_rejected_before_model_call(self) -> None:
        transport = FakeTransport(valid_output())
        generator = OpenAIStageGenerator(transport=transport)

        output = generator.generate(
            guided_session(),
            "请运行Python代码关闭课程助手",
        )

        self.assertTrue(output.stage_payload["request_rejected"])
        self.assertEqual(transport.requests, [])
        self.assertIn("我不能执行", output.assistant_message)

    def test_stage_one_rejects_mixed_in_uncataloged_alternative(self) -> None:
        options = KNOWLEDGE.brainstorm_options("研究传输线驻波", limit=1)
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "alternative_ideas": [
                            options[0],
                            {"direction": "Invented", "focus": "Invented experiment"},
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(),
                "研究传输线驻波",
            )

    def test_stage_one_rejects_later_stage_refinement_activity(self) -> None:
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "VARIABLE_SELECTION",
                        "alternative_ideas": KNOWLEDGE.brainstorm_options(
                            "研究传输线",
                            limit=1,
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(),
                "研究传输线",
            )

    def test_stage_one_rejects_implementation_terms_in_student_facing_text(self) -> None:
        transport = FakeTransport(
            valid_output(
                assistant_message="我根据knowledge_retrieval里的concept_id找到了方向。"
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(),
                "研究传输线驻波",
            )

    def test_out_of_scope_model_response_must_state_boundary(self) -> None:
        broad_options = retrieved_brainstorm_options("我想研究有机化学反应速率")
        broad_scenes = build_exploration_scenes(broad_options)
        missing_boundary = FakeTransport(
            valid_output(
                assistant_message="这里有三个课程方向供你选择。",
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "OUT_OF_SCOPE",
                        "alternative_ideas": broad_options,
                        "exploration_scenes": broad_scenes,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=missing_boundary).generate(
                guided_session(),
                "我想研究有机化学反应速率",
            )

    def test_ambiguous_preclassification_allows_model_semantic_course_judgment(self) -> None:
        broad_options = retrieved_brainstorm_options("我想研究一种材料里的变化")
        broad_scenes = build_exploration_scenes(broad_options)
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "这个表述可以从ECE329课内的场与材料关系继续辨析。下面用可改造、"
                    "可组合的物理图景启发思考；其中的启发性延伸不是课程结论。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "alternative_ideas": broad_options,
                        "exploration_scenes": broad_scenes,
                    },
                    ensure_ascii=False,
                ),
            )
        )

        output = OpenAIStageGenerator(transport=transport).generate(
            guided_session(),
            "我想研究一种材料里的变化",
        )

        self.assertEqual(output.stage_payload["input_category"], "COURSE_CONTENT")

    def test_model_selection_turn_removes_choice_list_and_requests_description(self) -> None:
        session = guided_session()
        options = KNOWLEDGE.brainstorm_options("研究传输线驻波", limit=3)
        session.history.append(
            {
                "handled_stage": Stage.IDEA_BRAINSTORMING.value,
                "user_message": "研究传输线驻波",
                "output": {
                    "stage_payload": {
                        "input_category": "COURSE_CONTENT",
                        "alternative_ideas": options,
                    }
                },
            }
        )
        session.design_context["idea"].update(
            {
                "topic_anchor": "研究传输线驻波",
                "current_focus": "研究传输线驻波",
                "focus_history": ["研究传输线驻波"],
                "course_scope_confirmed": True,
                "brainstorm_phase": "BREADTH_EXPLORATION",
            }
        )
        session.turn_context.update(
            build_stage_one_turn_context(
                str(options[0]["focus"]),
                options=options,
                idea_context=session.design_context["idea"],
                selected_option_id=str(options[0]["option_id"]),
            )
        )
        transport = FakeTransport(valid_output())

        output = OpenAIStageGenerator(transport=transport).generate(
            session,
            str(options[0]["focus"]),
        )

        self.assertEqual(output.stage_payload["brainstorm_phase"], "INTEREST_DESCRIPTION")
        self.assertEqual(output.stage_payload["alternative_ideas"], [])

    def test_selected_direction_rejects_a_new_visible_scene_list(self) -> None:
        session = guided_session()
        options = KNOWLEDGE.brainstorm_options("研究静电场中的两个源", limit=3)
        session.history.append(
            {
                "handled_stage": Stage.IDEA_BRAINSTORMING.value,
                "user_message": "研究静电场中的两个源",
                "output": {
                    "stage_payload": {
                        "input_category": "COURSE_CONTENT",
                        "alternative_ideas": options,
                    }
                },
            }
        )
        session.design_context["idea"].update(
            {
                "topic_anchor": "研究静电场中的两个源",
                "current_focus": "研究静电场中的两个源",
                "focus_history": ["研究静电场中的两个源"],
                "course_scope_confirmed": True,
                "brainstorm_phase": "BREADTH_EXPLORATION",
            }
        )
        session.turn_context.update(
            build_stage_one_turn_context(
                str(options[0]["focus"]),
                options=options,
                idea_context=session.design_context["idea"],
                selected_option_id=str(options[0]["option_id"]),
            )
        )
        invalid = valid_output()
        invalid["assistant_message"] = (
            "图景 A｜重新列出一个方向。图景 B｜再列出另一个方向。"
        )
        generator = OpenAIStageGenerator(
            transport=FakeTransport(invalid),
            repair_attempts=0,
        )

        with self.assertRaises(ModelOutputError):
            generator.generate(session, str(options[0]["focus"]))

    def test_model_depth_turn_uses_grounded_connections_without_choice_list(self) -> None:
        session = guided_session()
        options = KNOWLEDGE.brainstorm_options("研究传输线驻波", limit=3)
        session.design_context["idea"].update(
            {
                "topic_anchor": "研究传输线驻波",
                "current_focus": "研究传输线驻波 → 反射与驻波",
                "focus_history": ["研究传输线驻波", "反射与驻波"],
                "course_scope_confirmed": True,
                "brainstorm_phase": "INTEREST_DESCRIPTION",
                "selected_focus": "反射与驻波",
            }
        )
        message = "我想弄清入射波和反射波叠加后为什么形成驻波"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=options,
                idea_context=session.design_context["idea"],
            )
        )
        transport = FakeTransport(valid_output())

        output = OpenAIStageGenerator(transport=transport).generate(session, message)

        self.assertEqual(output.stage_payload["brainstorm_phase"], "DEPTH_EXPANSION")
        self.assertEqual(output.stage_payload["alternative_ideas"], [])
        expected_connections = retrieved_brainstorm_options(message, session)
        self.assertEqual(
            output.stage_payload["deepening_connections"],
            expected_connections,
        )

    def test_ready_combined_direction_rejects_another_forced_choice(self) -> None:
        session = guided_session()
        options = KNOWLEDGE.brainstorm_options(
            "静电场 电场线 电荷源",
            limit=3,
        )
        scenes = build_exploration_scenes(options)
        session.history.append(
            {
                "handled_stage": Stage.IDEA_BRAINSTORMING.value,
                "user_message": "我想研究静电场中的两个源",
                "output": {
                    "stage_payload": {
                        "input_category": "COURSE_CONTENT",
                        "alternative_ideas": options,
                        "exploration_scenes": scenes,
                    }
                },
            }
        )
        session.design_context["idea"].update(
            {
                "topic_anchor": "我想研究静电场中的两个源",
                "current_focus": "图景A与图景B的组合",
                "focus_history": [
                    "我想研究静电场中的两个源",
                    "图景A与图景B的组合",
                ],
                "course_scope_confirmed": True,
                "brainstorm_phase": "INTEREST_DESCRIPTION",
                "selected_scene_ids": ["scene_a", "scene_b"],
                "selected_course_relations": [options[0], options[1]],
                "combination_intent": True,
            }
        )
        message = "观察两个源靠近时电场线在中间区域的变化"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=options,
                scenes=scenes,
                idea_context=session.design_context["idea"],
            )
        )
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "这个方向可以继续细化。你想先看同种电荷还是异种电荷？"
                )
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(session, message)

    def test_model_can_propose_a_new_course_grounded_basic_case_bundle(self) -> None:
        session = guided_session()
        session.design_context["idea"].update(
            {
                "topic_anchor": "我想研究电磁波偏振",
                "current_focus": "电磁波偏振 → 偏振轨迹",
                "focus_history": ["我想研究电磁波偏振", "偏振轨迹"],
                "course_scope_confirmed": True,
                "brainstorm_phase": "INTEREST_DESCRIPTION",
                "selected_focus": "偏振轨迹",
            }
        )
        message = "我想理解正交分量的关系怎样形成不同的偏振轨迹"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=KNOWLEDGE.brainstorm_options("电磁波偏振", limit=3),
                idea_context=session.design_context["idea"],
            )
        )
        self.assertTrue(session.turn_context["ready_for_next_stage"])
        self.assertEqual(session.turn_context["standard_comparisons"], [])

        comparison = {
            "comparison_id": "wave_polarization_forms",
            "cases": ["线偏振", "圆偏振"],
            "recommended_cases": ["线偏振", "圆偏振"],
            "case_aliases": {"线偏振": [], "圆偏振": []},
            "role": "PROPOSED_BASELINE_COMPARISON",
            "adoption_status": "PENDING",
            "reason": "两种偏振形式可作为观察正交分量相位关系的基础参照。",
            "course_concept_ids": ["lecture_24"],
        }
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "当前方向是理解正交分量如何形成偏振轨迹。建议默认把线偏振与"
                    "圆偏振作为一组基本对照；确认当前概括即表示采纳，也可以直接删改。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "standard_comparisons": [comparison],
                    },
                    ensure_ascii=False,
                ),
                student_task=(
                    "如果概括准确，请确认当前方向并进入下一阶段；若有关键遗漏，"
                    "请直接指出遗漏。"
                ),
            )
        )

        output = OpenAIStageGenerator(transport=transport).generate(session, message)

        proposed = output.stage_payload["standard_comparisons"][0]
        self.assertEqual(proposed["cases"], ["线偏振", "圆偏振"])
        self.assertEqual(proposed["adoption_status"], "PENDING")
        self.assertEqual(proposed["proposal_source"], "COURSE_GROUNDED_MODEL")

    def test_model_basic_case_bundle_rejects_unretrieved_course_grounding(self) -> None:
        session = guided_session()
        session.design_context["idea"].update(
            {
                "topic_anchor": "我想研究电磁波偏振",
                "current_focus": "电磁波偏振 → 偏振轨迹",
                "focus_history": ["我想研究电磁波偏振", "偏振轨迹"],
                "course_scope_confirmed": True,
                "brainstorm_phase": "INTEREST_DESCRIPTION",
                "selected_focus": "偏振轨迹",
            }
        )
        message = "我想理解正交分量的关系怎样形成不同的偏振轨迹"
        session.turn_context.update(
            build_stage_one_turn_context(
                message,
                options=KNOWLEDGE.brainstorm_options("电磁波偏振", limit=3),
                idea_context=session.design_context["idea"],
            )
        )
        comparison = {
            "comparison_id": "invented_case_bundle",
            "cases": ["情形甲", "情形乙"],
            "recommended_cases": ["情形甲", "情形乙"],
            "reason": "声称有课程依据。",
            "course_concept_ids": ["lecture_999"],
        }
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "建议默认把情形甲与情形乙作为一组基本对照；确认当前概括即表示采纳。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "standard_comparisons": [comparison],
                    },
                    ensure_ascii=False,
                ),
                student_task=(
                    "如果概括准确，请确认当前方向并进入下一阶段；若有关键遗漏，"
                    "请直接指出遗漏。"
                ),
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(session, message)

    def test_valid_out_of_scope_model_response_keeps_three_course_examples(self) -> None:
        broad_options = retrieved_brainstorm_options("我想研究有机化学反应速率")
        broad_scenes = build_exploration_scenes(broad_options)
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "这个主题不属于ECE329课程范围。你可以改从电磁场、电磁波或"
                    "传输线中的关系开始探索。下面的物理图景可以改造或组合，"
                    "其中的启发性延伸不是课程结论。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "OUT_OF_SCOPE",
                        "alternative_ideas": broad_options,
                        "exploration_scenes": broad_scenes,
                    },
                    ensure_ascii=False,
                ),
            )
        )

        output = OpenAIStageGenerator(transport=transport).generate(
            guided_session(),
            "我想研究有机化学反应速率",
        )

        self.assertEqual(output.stage_payload["input_category"], "OUT_OF_SCOPE")
        self.assertEqual(len(output.stage_payload["alternative_ideas"]), 3)

    def test_stage_two_rejects_modified_lecture_reference(self) -> None:
        reference = KNOWLEDGE.concept_references("研究传输线驻波", limit=1)[0].copy()
        reference["title"] = "Invented course topic"
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {"course_references": [reference]},
                    ensure_ascii=False,
                )
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(stage_index=1),
                "映射传输线驻波",
            )

    def test_stage_two_displays_existing_mapping_without_direction_choice(self) -> None:
        reference = KNOWLEDGE.concept_references("研究传输线驻波", limit=1)[0]
        transport = FakeTransport(
            valid_output(
                assistant_message="现有实验大纲的主要课程支点是传输线驻波，辅助联系是边界反射。",
                student_task="请检查这段课程映射是否准确；若有遗漏请指出。",
                stage_payload_json=json.dumps(
                    {
                        "primary_course_anchor": reference,
                        "supporting_course_anchors": [],
                        "mapped_relationships": ["负载边界与驻波分布的关系"],
                        "mapping_explanation": "解释已确定实验方向与课程知识的联系。",
                        "course_references": [reference],
                    },
                    ensure_ascii=False,
                ),
            )
        )

        output = OpenAIStageGenerator(transport=transport).generate(
            guided_session(stage_index=1),
            "展示当前想法的课程映射",
        )

        self.assertEqual(output.stage_payload["primary_course_anchor"], reference)
        self.assertNotRegex(output.assistant_message, r"请选择|你希望把哪|选哪")

    def test_stage_two_rejects_reselecting_a_course_direction(self) -> None:
        reference = KNOWLEDGE.concept_references("研究传输线驻波", limit=1)[0]
        transport = FakeTransport(
            valid_output(
                assistant_message="你希望把哪一个课程方向作为主要理论核心？",
                stage_payload_json=json.dumps(
                    {
                        "primary_course_anchor": reference,
                        "course_references": [reference],
                    },
                    ensure_ascii=False,
                ),
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(stage_index=1),
                "展示当前想法的课程映射",
            )

    def test_stage_ten_rejects_fake_measured_data(self) -> None:
        transport = FakeTransport(
            valid_output(
                student_task="这条曲线符合你的理论预期吗？",
                visualization_json=json.dumps(
                    {
                        "data_type": "theoretical_prediction",
                        "measured": True,
                    }
                ),
            )
        )
        generator = OpenAIStageGenerator(transport=transport)

        with self.assertRaises(ModelOutputError):
            generator.generate(guided_session(stage_index=9), "生成窗口")

    def test_stage_five_rejects_uncataloged_formula(self) -> None:
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "core_equations": [
                            {"id": "invented_formula", "expression": "E=mc^2", "pages": [999]}
                        ]
                    }
                )
            )
        )
        generator = OpenAIStageGenerator(transport=transport)

        with self.assertRaises(ModelOutputError):
            generator.generate(guided_session(stage_index=4), "选择传输线理论公式")

    def test_stage_five_rejects_wrong_pages_for_cataloged_formula(self) -> None:
        formula = KNOWLEDGE.formula_references("传输线驻波", limit=1)[0].copy()
        formula["pages"] = [999]
        transport = FakeTransport(
            valid_output(stage_payload_json=json.dumps({"core_equations": [formula]}))
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(stage_index=4),
                "选择传输线理论公式",
            )

    def test_stage_five_rejects_formula_without_stable_id(self) -> None:
        formula = KNOWLEDGE.formula_references("研究传输线驻波", limit=1)[0]
        transport = FakeTransport(
            valid_output(
                stage_payload_json=json.dumps(
                    {
                        "core_equations": [
                            formula,
                            {"expression": "invented", "pages": formula["pages"]},
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        )

        with self.assertRaises(ModelOutputError):
            OpenAIStageGenerator(transport=transport).generate(
                guided_session(stage_index=4),
                "选择传输线理论公式",
            )

    def test_service_failure_uses_rule_based_fallback(self) -> None:
        transport = FakeTransport(error=ModelServiceError("temporary"))
        fallback = FallbackStageGenerator(
            primary=OpenAIStageGenerator(transport=transport),
            fallback=RuleBasedStageGenerator(),
        )

        output = fallback.generate(guided_session(), "研究传输线驻波")

        self.assertTrue(output.stage_payload["alternative_ideas"])
        self.assertEqual(output.warnings, [])
        info = fallback.runtime_info()
        self.assertEqual(info["last_fallback_reason"], "model_transport_error")
        self.assertEqual(info["fallback_calls"], 1)

    def test_invalid_model_output_is_repaired_once_before_fallback(self) -> None:
        class SequencedTransport:
            def __init__(self) -> None:
                self.requests: list[dict[str, Any]] = []
                self.outputs = [
                    {"assistant_message": "invalid"},
                    valid_output(),
                ]

            def create(self, payload: dict[str, Any]) -> dict[str, Any]:
                self.requests.append(deepcopy(payload))
                output = self.outputs.pop(0)
                return {
                    "id": f"resp_{len(self.requests)}",
                    "output_text": json.dumps(output, ensure_ascii=False),
                }

        transport = SequencedTransport()
        generator = OpenAIStageGenerator(transport=transport)

        output = generator.generate(guided_session(), "研究传输线驻波")

        self.assertEqual(output.stage_payload["input_category"], "COURSE_CONTENT")
        self.assertEqual(len(transport.requests), 2)
        self.assertNotIn("previous_response_id", transport.requests[1])
        info = generator.runtime_info()
        self.assertEqual(info["output_rejections"], 1)
        self.assertEqual(info["repair_successes"], 1)

    def test_output_rejection_reason_is_visible_in_health_metrics(self) -> None:
        fallback = FallbackStageGenerator(
            primary=OpenAIStageGenerator(
                transport=FakeTransport({"assistant_message": "invalid"}),
                repair_attempts=0,
            ),
            fallback=RuleBasedStageGenerator(),
        )

        fallback.generate(guided_session(), "研究传输线驻波")

        info = fallback.runtime_info()
        self.assertEqual(info["last_fallback_reason"], "model_output_rejected")
        self.assertEqual(info["output_rejections"], 1)

    def test_auto_mode_without_key_remains_rule_based(self) -> None:
        generator = generator_from_environment({})

        self.assertIsInstance(generator, RuleBasedStageGenerator)
        self.assertEqual(generator.runtime_info()["provider"], "rule_based")

    def test_auto_mode_with_transport_enables_openai_without_real_call(self) -> None:
        transport = FakeTransport(valid_output())
        generator = generator_from_environment(
            {
                "OPENAI_MODEL": "test-model",
                "ECE329_OPENAI_FALLBACK": "true",
            },
            transport=transport,
        )

        self.assertIsInstance(generator, FallbackStageGenerator)
        self.assertEqual(generator.runtime_info()["model"], "test-model")

    def test_environment_can_enable_responses_api_continuity(self) -> None:
        generator = generator_from_environment(
            {
                "ECE329_OPENAI_STATEFUL": "true",
                "ECE329_OPENAI_FALLBACK": "false",
            },
            transport=FakeTransport(valid_output()),
        )

        self.assertIsInstance(generator, OpenAIStageGenerator)
        self.assertTrue(generator.runtime_info()["stateful"])

    def test_engine_reports_generator_without_exposing_credentials(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())

        self.assertEqual(
            engine.generator_info(),
            {"provider": "rule_based", "model": None, "fallback_enabled": False},
        )


if __name__ == "__main__":
    unittest.main()
