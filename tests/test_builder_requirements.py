from __future__ import annotations

import unittest

from ece329_workflow.builder_requirements import (
    BUILDER_REQUIREMENT_FIELDS,
    BUILDER_REQUIREMENT_SPECS,
    builder_handoff_status,
    missing_builder_requirements,
    next_due_builder_requirement,
    validate_builder_requirements,
)
from ece329_workflow.dialogue_acts import STAGE_ACT_FIELD_ORDER, STAGE_ACT_FIELDS
from ece329_workflow.models import DesignSession, InteractionState, Stage


VALID_VALUES = {
    "lab_title": "双电荷电场线交互实验",
    "lab_id": "ece329_charge_field",
    "builder_workspace_absolute_path": r"E:\暑研\EMVR_Blind_BuilderPack",
    "desktop_interaction_plan": "单击带电体进行选择并拖动位置；VR中映射为射线选择和手柄抓取。",
    "room_spatial_requirements": "学生站在中央，实验对象在前方，面板分列两侧并保留绕行空间。",
    "hidden_object_lifecycle": "无",
    "initial_reset_state": (
        "Initial与Reset均恢复为异号电荷，Q1=+1 μC、Q2=-1 μC、距离1.0 m，"
        "探针位于中点并清除曲线与快照。"
    ),
    "parameter_specifications": (
        "距离通过拖动可调：最小0.2 m、最大2.0 m、默认1.0 m、步长0.1 m；"
        "电荷配置为可调离散选项，默认异号，允许选项为同号、异号。"
    ),
    "model_constants_and_media": (
        "固定真空介电常数ε₀=8.8541878128e-12 F/m；介质固定为真空，εr=1无量纲。"
    ),
    "measurement_specifications": (
        "场线形态指标定义为中垂线采样点的平均方向变化角，单位度；空间探针显示E矢量分量、"
        "场强和方向，单位V/m，并在参数变化后重新计算。"
    ),
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

    def test_requirement_registry_is_the_dialogue_write_source_of_truth(self) -> None:
        registered = tuple(str(item["field"]) for item in BUILDER_REQUIREMENT_SPECS)

        self.assertEqual(len(registered), len(set(registered)))
        self.assertEqual(set(registered), set(BUILDER_REQUIREMENT_FIELDS))
        self.assertTrue(BUILDER_REQUIREMENT_FIELDS <= STAGE_ACT_FIELDS)
        self.assertEqual(
            tuple(field for field in STAGE_ACT_FIELD_ORDER if field in BUILDER_REQUIREMENT_FIELDS),
            registered,
        )
        self.assertTrue(
            all(isinstance(item["stage"], Stage) for item in BUILDER_REQUIREMENT_SPECS)
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

    def test_relative_workspace_and_incomplete_runtime_contracts_remain_due(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values.update(
            {
                "builder_workspace_absolute_path": "EMVR_Blind_BuilderPack",
                "initial_reset_state": "初始采用异号电荷",
                "model_constants_and_media": "使用真空介质",
                "measurement_specifications": "显示电场",
            }
        )
        session.design_context["stage_design_state"] = values

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertEqual(
            missing,
            {
                "builder_workspace_absolute_path",
                "initial_reset_state",
                "model_constants_and_media",
                "measurement_specifications",
            },
        )

    def test_invalid_id_explains_the_exact_format_problem(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = {
            **VALID_VALUES,
            "lab_id": "ece329_charge_field——0",
        }

        requirement = next_due_builder_requirement(
            session, Stage.COURSE_MAPPING_AND_DIRECTION
        )

        self.assertEqual(requirement["field"], "lab_id")
        self.assertIn("ece329_charge_field——0", requirement["validation_error"])
        self.assertIn("只能包含小写字母、数字和下划线", requirement["validation_error"])

    def test_chinese_units_and_discrete_options_complete_parameter_requirement(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = {
            **VALID_VALUES,
            "parameter_specifications": (
                "距离由滑块可调：最小值0.5米，最大值5米，默认值2米，建议步长0.1米；"
                "电荷类型为可调离散选项，默认异种电荷，全部允许选项为同种电荷、异种电荷。"
            ),
        }

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertNotIn("parameter_specifications", missing)

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
