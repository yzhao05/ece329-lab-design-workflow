from __future__ import annotations

import unittest

from ece329_workflow.builder_requirements import (
    BUILDER_REQUIREMENT_SPECS,
    builder_handoff_status,
    missing_builder_requirements,
    next_due_builder_requirement,
    validate_builder_requirements,
)
from ece329_workflow.models import DesignSession, InteractionState, Stage


VALID_VALUES = {
    "lab_title": "双电荷电场线交互实验",
    "lab_id": "ece329_charge_field",
    "desktop_interaction_plan": "单击带电体进行选择并拖动位置；VR中映射为射线选择和手柄抓取。",
    "room_spatial_requirements": "学生站在中央，实验对象在前方，面板分列两侧并保留绕行空间。",
    "hidden_object_lifecycle": "无",
    "parameter_specifications": "距离0.2 m至2.0 m，步长0.1 m",
    "expected_results": "同种与异种电荷在靠近时呈现不同的场线弯曲和连接关系。",
    "acceptance_criteria": "完成两种极性配置的距离扫描并保存可比较结果。",
    "report_questions": "两种配置的中间区域场线为何不同？",
}


class BuilderRequirementTests(unittest.TestCase):
    def _session(self) -> DesignSession:
        return DesignSession(
            design_id="design_builder_requirements",
            interaction_state=InteractionState.EMVR_DIRECT,
        )

    def test_all_builder_inputs_are_required_before_handoff(self) -> None:
        session = self._session()
        self.assertEqual(
            {item["field"] for item in missing_builder_requirements(session)},
            set(VALID_VALUES),
        )
        session.design_context["stage_design_state"] = dict(VALID_VALUES)
        self.assertEqual(missing_builder_requirements(session), [])
        self.assertTrue(builder_handoff_status(session)["ready"])
        validate_builder_requirements(session)

    def test_invalid_id_and_non_numeric_parameter_range_remain_due(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values["lab_id"] = "Bad ID"
        values["parameter_specifications"] = "之后再决定范围"
        session.design_context["stage_design_state"] = values
        missing = {item["field"] for item in missing_builder_requirements(session)}
        self.assertEqual(missing, {"lab_id", "parameter_specifications"})

    def test_placeholder_text_cannot_complete_final_artifact_fields(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values["lab_title"] = "待确认"
        values["expected_results"] = "暂未明确，之后补充"
        session.design_context["stage_design_state"] = values

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertEqual(missing, {"lab_title", "expected_results"})

    def test_earlier_requirement_is_recovered_after_mode_switch(self) -> None:
        session = self._session()
        requirement = next_due_builder_requirement(
            session, Stage.RESULT_INTERPRETATION
        )
        self.assertIsNotNone(requirement)
        self.assertEqual(requirement["field"], BUILDER_REQUIREMENT_SPECS[0]["field"])

    def test_guided_mode_has_no_builder_handoff_requirements(self) -> None:
        session = DesignSession(
            design_id="design_guided_requirements",
            interaction_state=InteractionState.GUIDED_DESIGN,
        )
        self.assertEqual(missing_builder_requirements(session), [])
        self.assertEqual(builder_handoff_status(session)["completed"], 0)


if __name__ == "__main__":
    unittest.main()
