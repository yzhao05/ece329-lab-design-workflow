from __future__ import annotations

import json
import unittest
from pathlib import Path

from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stage_one_dialogues.json"


class StageOneDialogueEvalTests(unittest.TestCase):
    def test_dialogue_regression_cases(self) -> None:
        cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                class FixtureSemanticGenerator(RuleBasedStageGenerator):
                    selected_ids: list[str] = []

                    def resolve_intent(
                        self,
                        session,
                        user_message,
                        pending_action,
                        carried_context,
                    ):
                        return resolved_intent(
                            UserIntent.ANSWER_CURRENT_QUESTION,
                            confidence=0.98,
                            source="SEMANTIC_TEST",
                            semantic_updates={
                                "no_direction": case["id"] == "no_direction",
                                "selected_option_ids": self.selected_ids,
                            },
                        )

                generator = FixtureSemanticGenerator()
                engine = WorkflowEngine(generator=generator)
                first = engine.create_design(case["initial"])
                result = first
                selected = None
                if case.get("followup"):
                    selected = first["stage_payload"]["alternative_ideas"][
                        case["selected_index"]
                    ]
                    generator.selected_ids = [selected["option_id"]]
                    request = {"message": case["followup"]}
                    if case.get("send_option_id"):
                        request["selected_option_id"] = selected["option_id"]
                    result = engine.process_turn(first["design_id"], request)

                payload = result["stage_payload"]
                self.assertEqual(payload["input_category"], case["expected_category"])
                if "request_rejected" in case:
                    self.assertIs(
                        payload.get("request_rejected", False),
                        case["request_rejected"],
                    )
                if case.get("message_contains"):
                    self.assertIn(case["message_contains"], result["assistant_message"])
                if case.get("expected_mode"):
                    self.assertEqual(result["interaction_state"], case["expected_mode"])
                if selected is not None:
                    self.assertEqual(payload["resolved_option_reference"], selected)
                    self.assertNotIn("不属于ECE329", result["assistant_message"])


if __name__ == "__main__":
    unittest.main()
