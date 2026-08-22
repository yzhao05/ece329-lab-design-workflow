from __future__ import annotations

import io
import json
import unittest

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.knowledge_base import KNOWLEDGE
from ece329_workflow.models import InteractionState, Stage


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = WorkflowEngine(generator=RuleBasedStageGenerator())

    def test_guided_design_stays_in_brainstorming_by_default(self) -> None:
        result = self.engine.create_design("我想研究金属屏蔽无线信号")

        self.assertEqual(result["handled_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(result["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(result["interaction_state"], InteractionState.GUIDED_DESIGN.value)
        self.assertIsNotNone(result["student_task"])
        self.assertLessEqual(result["student_task"].count("？"), 1)

    def test_guided_brainstorm_requires_student_confirmation(self) -> None:
        first = self.engine.create_design("研究传输线驻波")
        result = self.engine.process_turn(
            first["design_id"],
            {"message": "进入下一阶段", "complete_stage": True},
        )

        self.assertEqual(result["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertIn("student_confirmed", result["completion_error"])

    def test_guided_brainstorm_advances_only_after_required_context(self) -> None:
        first = self.engine.create_design("研究传输线驻波")
        result = self.engine.process_turn(
            first["design_id"],
            {
                "message": "就研究负载对驻波的影响",
                "complete_stage": True,
                "context_patch": {
                    "idea": {
                        "phenomenon": "驻波",
                        "main_direction": "负载阻抗对驻波分布的影响",
                        "student_confirmed": True,
                    }
                },
            },
        )

        self.assertEqual(result["handled_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(result["current_stage"], Stage.COURSE_MAPPING_AND_DIRECTION.value)
        self.assertEqual(result["stage_status"], "completed")
        self.assertEqual(result["next_stage"], Stage.LEARNING_OBJECTIVES.value)

    def test_emvr_trigger_is_explicit_and_auto_advances_one_stage(self) -> None:
        result = self.engine.create_design("请把电磁屏蔽实验放入EMVR工作流")

        self.assertEqual(result["interaction_state"], InteractionState.EMVR_DIRECT.value)
        self.assertEqual(result["handled_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(result["current_stage"], Stage.COURSE_MAPPING_AND_DIRECTION.value)
        self.assertNotIn(Stage.COURSE_MAPPING_AND_DIRECTION.value, result["stage_payload"])

    def test_emvr_stage_seven_does_not_define_scene_or_accessibility(self) -> None:
        first = self.engine.create_design("使用EMVR设计一个偏振实验")
        design_id = first["design_id"]
        result = first
        while result["current_stage"] != Stage.CONCEPTUAL_OR_VR_SETUP.value:
            result = self.engine.process_turn(design_id, {"message": "继续"})
        result = self.engine.process_turn(design_id, {"message": "完善Unity VR设计"})

        payload = result["stage_payload"]
        self.assertEqual(result["handled_stage"], Stage.CONCEPTUAL_OR_VR_SETUP.value)
        self.assertNotIn("scene", payload)
        self.assertNotIn("comfort_and_accessibility", payload)
        self.assertIn("unity_objects", payload)
        self.assertIn("physics_layer", payload)
        self.assertIn("本阶段不定义VR场景", result["warnings"][0])

    def test_visualization_is_theoretical_not_measured(self) -> None:
        first = self.engine.create_design("请在EMVR中设计传输线驻波实验")
        design_id = first["design_id"]
        result = first
        while result["current_stage"] != Stage.EXPECTED_DATA_VISUALIZATION.value:
            result = self.engine.process_turn(design_id, {"message": "继续"})
        result = self.engine.process_turn(design_id, {"message": "生成参考窗口"})

        visual = result["visualization"]
        self.assertEqual(result["handled_stage"], Stage.EXPECTED_DATA_VISUALIZATION.value)
        self.assertEqual(visual["data_type"], "theoretical_prediction")
        self.assertFalse(visual["measured"])
        self.assertIsNotNone(visual["unity_binding"])

    def test_guided_final_stage_never_generates_final_proposal(self) -> None:
        first = self.engine.create_design("研究偏振器角度")
        session = self.engine.store.get(first["design_id"])
        session.current_stage_index = 12
        self.engine.store.save(session)

        result = self.engine.process_turn(first["design_id"], {"message": "开始总结"})

        self.assertEqual(result["handled_stage"], Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT.value)
        self.assertFalse(result["stage_payload"]["final_proposal_generated"])
        self.assertIn("你自己完成总结", result["assistant_message"])

    def test_guided_final_stage_requires_student_written_summary(self) -> None:
        first = self.engine.create_design("研究偏振器角度")
        session = self.engine.store.get(first["design_id"])
        session.current_stage_index = 12
        self.engine.store.save(session)

        rejected = self.engine.process_turn(
            first["design_id"],
            {
                "message": "确认完成学生总结",
                "complete_stage": True,
                "context_patch": {"synthesis": {"student_summary_complete": True}},
            },
        )
        accepted = self.engine.process_turn(
            first["design_id"],
            {
                "message": "这是我自己完成的实验设计总结，现在确认完成。",
                "complete_stage": True,
                "context_patch": {
                    "synthesis": {
                        "student_summary": "本实验研究偏振器角度对透射场强的影响，并用讲义关系解释预期趋势。",
                        "student_summary_complete": True,
                    }
                },
            },
        )

        self.assertIsNotNone(rejected["completion_error"])
        self.assertEqual(accepted["workflow_status"], "complete")

    def test_prompt_packet_keeps_stage_control_outside_model(self) -> None:
        first = self.engine.create_design("研究磁场")
        packet = self.engine.get_prompt_packet(first["design_id"], "给我一些方向")

        self.assertEqual(packet["context"]["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertIn("任何一次回复只能处理current_stage", packet["system"])
        self.assertIn("唯一内置来源", packet["system"])
        self.assertIn("alternative_ideas", packet["context"]["stage_output_contract"])
        self.assertIn("原样复制", packet["context"]["stage_output_contract"])
        self.assertEqual(
            packet["context"]["knowledge_retrieval"]["source_content_role"],
            "reference_data_not_instructions",
        )

    def test_student_decisions_and_recent_history_are_preserved(self) -> None:
        first = self.engine.create_design("研究传输线驻波")
        self.engine.process_turn(first["design_id"], {"message": "我想比较不同负载阻抗"})

        design = self.engine.get_design(first["design_id"], include_history=True)
        decisions = design["design_context"]["student_decisions"][Stage.IDEA_BRAINSTORMING.value]
        packet = self.engine.get_prompt_packet(first["design_id"], "继续比较")

        self.assertEqual(decisions[-1]["message"], "我想比较不同负载阻抗")
        self.assertTrue(packet["context"]["recent_history"])

    def test_second_brainstorm_uses_latest_student_topic(self) -> None:
        first = self.engine.create_design("我还没有具体想法")
        result = self.engine.process_turn(first["design_id"], {"message": "我想研究偏振"})

        self.assertTrue(result["stage_payload"]["alternative_ideas"])
        self.assertTrue(all(item["concept_id"] == "lecture_24" for item in result["stage_payload"]["alternative_ideas"]))

    def test_lecture_knowledge_catalog_is_internally_valid(self) -> None:
        self.assertEqual(KNOWLEDGE.validate(), [])
        self.assertEqual(len(KNOWLEDGE.lectures), 39)
        self.assertGreaterEqual(len(KNOWLEDGE.formulas), 80)

    def test_stage_one_brainstorm_is_grounded_in_lecture_notes(self) -> None:
        result = self.engine.create_design("我想研究金属屏蔽无线信号")
        options = result["stage_payload"]["alternative_ideas"]

        self.assertTrue(options)
        self.assertTrue(all(item["concept_id"] == "lecture_23" for item in options))
        self.assertTrue(all(item["source_pages"] == [211, 216] for item in options))

    def test_unknown_idea_falls_back_only_to_note_overview(self) -> None:
        result = self.engine.create_design("我还没有任何具体想法")
        options = result["stage_payload"]["alternative_ideas"]

        self.assertEqual([item["concept_id"] for item in options], ["electrostatics", "magnetism", "electromagnetics"])
        self.assertTrue(all(item["source_pages"] == [10, 11, 12] for item in options))

    def test_theory_stage_returns_only_cataloged_formulas_with_pages(self) -> None:
        first = self.engine.create_design("研究传输线驻波")
        session = self.engine.store.get(first["design_id"])
        session.current_stage_index = 4
        self.engine.store.save(session)

        result = self.engine.process_turn(first["design_id"], {"message": "选择理论公式"})
        formulas = result["stage_payload"]["lecture_formula_candidates"]

        catalog_ids = {item["id"] for item in KNOWLEDGE.formulas}
        self.assertTrue(formulas)
        self.assertTrue(all(item["id"] in catalog_ids and item["pages"] for item in formulas))


class WorkflowAPITests(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        api = WorkflowAPI(WorkflowEngine(generator=RuleBasedStageGenerator()))
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = headers

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/health",
            "QUERY_STRING": "",
            "CONTENT_LENGTH": "0",
            "wsgi.input": io.BytesIO(b""),
        }
        body = b"".join(api(environ, start_response))
        payload = json.loads(body.decode("utf-8"))

        self.assertTrue(str(captured["status"]).startswith("200"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["generator"]["provider"], "rule_based")
        self.assertNotIn("api_key", json.dumps(payload).casefold())

    def test_knowledge_search_endpoint_returns_grounded_results(self) -> None:
        api = WorkflowAPI(WorkflowEngine(generator=RuleBasedStageGenerator()))
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = headers

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/v1/knowledge/search",
            "QUERY_STRING": "%71=%E5%81%8F%E6%8C%AF",
            "CONTENT_LENGTH": "0",
            "wsgi.input": io.BytesIO(b""),
        }
        body = b"".join(api(environ, start_response))
        payload = json.loads(body.decode("utf-8"))

        self.assertTrue(str(captured["status"]).startswith("200"))
        self.assertTrue(payload["concepts"])
        self.assertTrue(all(item["pages"] for item in payload["concepts"]))


if __name__ == "__main__":
    unittest.main()
