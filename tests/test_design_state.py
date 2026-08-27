from __future__ import annotations

import unittest

from ece329_workflow.design_state import (
    apply_design_updates,
    design_state_snapshot,
    record_seen_scenes,
    seen_scene_signatures,
)
from ece329_workflow.dialogue_state import (
    UserIntent,
    current_pending_action,
    resolved_intent,
    save_pending_action,
)
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator, build_exploration_scenes
from ece329_workflow.models import DesignSession, InteractionState, Stage, StepOutput


class SummarySemanticGenerator(RuleBasedStageGenerator):
    def resolve_intent(self, session, user_message, pending_action, carried_context):
        return resolved_intent(
            UserIntent.REQUEST_CURRENT_DESIGN_SUMMARY,
            target="current_design",
            confidence=0.99,
            source="SEMANTIC_TEST",
        )


class CanonicalDesignStateTests(unittest.TestCase):
    def test_scene_templates_are_excluded_across_rounds(self) -> None:
        session = DesignSession(
            design_id="design_scene_history",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        options = [
            {
                "option_id": f"option_{index}",
                "direction": "静电场与场源分布",
                "focus": "电荷分布与空间场形状之间的关系",
            }
            for index in range(3)
        ]
        first = build_exploration_scenes(options)
        record_seen_scenes(session, first)
        second = build_exploration_scenes(
            options,
            excluded_scene_signatures=seen_scene_signatures(session),
        )

        first_ids = {scene["scene_template_id"] for scene in first}
        second_ids = {scene["scene_template_id"] for scene in second}
        self.assertTrue(
            first_ids.isdisjoint(second_ids),
            f"first={first_ids}, second={second_ids}",
        )
        public_session = session.to_dict()
        self.assertNotIn(
            "seen_scene_template_ids",
            public_session["design_context"]["design_state"],
        )

    def test_pre_upgrade_scene_history_is_migrated_before_next_batch(self) -> None:
        session = DesignSession(
            design_id="design_legacy_scene_history",
            interaction_state=InteractionState.GUIDED_DESIGN,
            history=[
                {
                    "output": {
                        "stage_payload": {
                            "exploration_scenes": [
                                {
                                    "title": "让两个场源从远处慢慢靠近",
                                }
                            ]
                        }
                    }
                }
            ],
        )
        excluded = seen_scene_signatures(session)
        scenes = build_exploration_scenes(
            [
                {
                    "option_id": "new_option",
                    "direction": "静电场与场源分布",
                    "focus": "电荷分布与空间场形状之间的关系",
                }
            ],
            excluded_scene_signatures=excluded,
        )

        self.assertNotEqual(
            scenes[0]["title"],
            "让两个场源从远处慢慢靠近",
        )

    def test_merge_updates_are_idempotent(self) -> None:
        session = DesignSession(
            design_id="design_idempotent_merge",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        pending = {"action_id": "action_concept", "subject": "conceptual_structure"}
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "REPLACE",
                    "value": "显示电场线",
                }
            ],
            pending_action=pending,
        )
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "MERGE",
                    "value": "增加等势面显示",
                }
            ],
            pending_action=pending,
        )
        revision_after_first_merge = design_state_snapshot(session)["revision"]
        apply_design_updates(
            session,
            [
                {
                    "field": "conceptual_structure",
                    "operation": "MERGE",
                    "value": "增加等势面显示",
                }
            ],
            pending_action=pending,
        )

        state = design_state_snapshot(session)
        self.assertEqual(state["revision"], revision_after_first_merge)
        self.assertEqual(
            state["conceptual_structure"].count("增加等势面显示"),
            1,
        )
        self.assertIn("显示电场线", state["conceptual_structure"])

    def test_read_only_summary_includes_learning_objective_and_preserves_pending(self) -> None:
        engine = WorkflowEngine(generator=SummarySemanticGenerator())
        session = DesignSession(
            design_id="design_read_only_summary",
            interaction_state=InteractionState.GUIDED_DESIGN,
            current_stage_index=list(Stage).index(Stage.VARIABLES_AND_CONDITIONS),
        )
        apply_design_updates(
            session,
            [
                {"field": "research_object", "operation": "REPLACE", "value": "两个点电荷"},
                {"field": "course_relationship", "operation": "REPLACE", "value": "静电场叠加"},
                {
                    "field": "learning_objective",
                    "operation": "REPLACE",
                    "value": "解释距离和极性怎样改变场线分布",
                },
                {
                    "field": "expected_phenomenon",
                    "operation": "REPLACE",
                    "value": "靠近时中间区域的场线重新分布",
                },
                {
                    "field": "theoretical_framework",
                    "operation": "REPLACE",
                    "value": "电场叠加与高斯定律",
                },
            ],
        )
        save_pending_action(
            session,
            session.current_stage,
            StepOutput(
                assistant_message="我们继续完善变量。",
                stage_payload={"proposal": {"stage": session.current_stage.value}},
                student_task="你认为哪些量需要改变和观察？",
            ),
        )
        before = current_pending_action(session)
        assert before is not None
        engine.store.save(session)

        result = engine.process_turn(
            session.design_id,
            {"message": "把研究对象、课程关系、学习目标和预期现象逐项列出来"},
        )

        self.assertTrue(result["stage_payload"]["read_only_design_summary"])
        self.assertIn("学习目标：解释距离和极性怎样改变场线分布", result["assistant_message"])
        self.assertIn("理论依据：电场叠加与高斯定律", result["assistant_message"])
        self.assertIn("预期现象：靠近时中间区域的场线重新分布", result["assistant_message"])
        self.assertNotIn(
            "seen_scene_template_ids",
            result["stage_payload"]["design_state"],
        )
        after = current_pending_action(engine.store.get(session.design_id))
        assert after is not None
        self.assertEqual(after["action_id"], before["action_id"])


if __name__ == "__main__":
    unittest.main()
