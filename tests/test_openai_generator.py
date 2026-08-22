from __future__ import annotations

import json
import unittest
from typing import Any

from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.openai_generator import (
    FallbackStageGenerator,
    ModelOutputError,
    ModelServiceError,
    OpenAIStageGenerator,
    generator_from_environment,
)


class FakeTransport:
    def __init__(self, output: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        if self.error:
            raise self.error
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(self.output, ensure_ascii=False),
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
    brainstorm = KNOWLEDGE.brainstorm_options("研究传输线驻波", limit=1)
    output: dict[str, Any] = {
        "assistant_message": "我们先比较ECE329课上所学概念之间的关系。",
        "stage_payload_json": json.dumps(
            {
                "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                "input_category": "COURSE_CONTENT",
                "alternative_ideas": brainstorm,
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


class OpenAIStageGeneratorTests(unittest.TestCase):
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

    def test_out_of_scope_model_response_must_state_boundary_and_keep_category(self) -> None:
        broad_options = KNOWLEDGE.broad_entry_points()
        missing_boundary = FakeTransport(
            valid_output(
                assistant_message="这里有三个课程方向供你选择。",
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "OUT_OF_SCOPE",
                        "alternative_ideas": broad_options,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        upgraded_to_course = FakeTransport(
            valid_output(
                assistant_message="这是ECE329课程内容。",
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "COURSE_CONTENT",
                        "alternative_ideas": broad_options,
                    },
                    ensure_ascii=False,
                ),
            )
        )

        for transport in (missing_boundary, upgraded_to_course):
            with self.subTest(assistant_message=transport.output["assistant_message"]):
                with self.assertRaises(ModelOutputError):
                    OpenAIStageGenerator(transport=transport).generate(
                        guided_session(),
                        "我想研究有机化学反应速率",
                    )

    def test_valid_out_of_scope_model_response_keeps_three_course_examples(self) -> None:
        broad_options = KNOWLEDGE.broad_entry_points()
        transport = FakeTransport(
            valid_output(
                assistant_message=(
                    "这个主题不属于ECE329课程范围。你可以改从电磁场、电磁波或"
                    "传输线中的关系开始探索。"
                ),
                stage_payload_json=json.dumps(
                    {
                        "brainstorm_activity": "RELATIONSHIP_DISCOVERY",
                        "input_category": "OUT_OF_SCOPE",
                        "alternative_ideas": broad_options,
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
        self.assertIn("ECE329课程资料", output.warnings[-1])
        self.assertNotIn("生成器", output.warnings[-1])

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

    def test_engine_reports_generator_without_exposing_credentials(self) -> None:
        engine = WorkflowEngine(generator=RuleBasedStageGenerator())

        self.assertEqual(
            engine.generator_info(),
            {"provider": "rule_based", "model": None, "fallback_enabled": False},
        )


if __name__ == "__main__":
    unittest.main()
