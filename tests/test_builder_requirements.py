from __future__ import annotations

import unittest

from ece329_workflow.builder_requirements import (
    BUILDER_REQUIREMENT_FIELDS,
    BUILDER_REQUIREMENT_SPECS,
    builder_handoff_status,
    builder_requirement_values,
    missing_builder_requirements,
    next_due_builder_requirement,
    validate_builder_requirements,
)
from ece329_workflow.builder_defaults import (
    build_implementation_defaults,
    format_implementation_defaults,
    implementation_defaults_approval_valid,
    record_implementation_defaults_approval,
)
from ece329_workflow.dialogue_acts import (
    STAGE_ACT_FIELD_ORDER,
    STAGE_ACT_FIELDS,
    apply_stage_field_updates,
    reconcile_stage_clear_markers,
)
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
    "numerical_model_specifications": (
        "直接计算库仑场；计算域[-3,3] m，采样21^3网格；场线以RK4积分，"
        "每正电荷32个均匀球面种子，步长0.02 m，最大2048步；"
        "零场与0.05 m排除区及边界停止，误差容差1e-5；参数释放后刷新。"
    ),
    "acceptance_criteria": "完成两种极性配置的距离扫描并保存可比较结果。",
    "report_questions": "两种配置的中间区域场线为何不同？",
    "implementation_defaults": (
        "Start -> Lab -> Back；Lab固定提供Capture、Restore、Reset；Unity状态机和事件链逐步执行；"
        "界面提供帮助并使用English；测量、日志及桌面与XR映射均按已确认实验契约实现。"
    ),
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
            set(VALID_VALUES) - {"builder_workspace_absolute_path"},
        )
        session.design_context["stage_design_state"] = dict(VALID_VALUES)
        record_implementation_defaults_approval(session, source="TEST")
        self.assertEqual(missing_builder_requirements(session), [])
        self.assertTrue(builder_handoff_status(session)["ready"])
        validate_builder_requirements(session)

    def test_invalid_id_and_non_numeric_parameter_range_remain_due(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values["lab_id"] = "Bad ID"
        values["parameter_specifications"] = "之后再决定范围"
        session.design_context["stage_design_state"] = values
        record_implementation_defaults_approval(session, source="TEST")
        missing = {item["field"] for item in missing_builder_requirements(session)}
        self.assertEqual(missing, {"lab_id", "parameter_specifications"})

    def test_portable_workspace_is_ready_but_incomplete_design_contracts_remain_due(self) -> None:
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
        record_implementation_defaults_approval(session, source="TEST")

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertEqual(
            missing,
            {
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
        record_implementation_defaults_approval(session, source="TEST")

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertEqual(missing, {"lab_title", "expected_results"})

    def test_default_implementation_is_shown_then_invalidated_by_a_design_change(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values.pop("implementation_defaults")
        session.design_context["stage_design_state"] = values

        requirement = next_due_builder_requirement(
            session,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
        )

        self.assertEqual(requirement["field"], "implementation_defaults")
        self.assertIn("Start -> Lab -> Back", requirement["default_value"])
        self.assertIn("Unity事件链", requirement["default_value"])
        self.assertEqual(
            requirement["default_option_id"],
            "approve-builder-implementation-defaults",
        )

        values["implementation_defaults"] = requirement["default_value"]
        record_implementation_defaults_approval(session, source="TEST")
        self.assertNotIn(
            "implementation_defaults",
            {item["field"] for item in missing_builder_requirements(session)},
        )

        values["parameter_specifications"] = (
            "距离可调：最小0.3 m、最大3.0 m、默认1.5 m、步长0.1 m；"
            "配置为离散可调选项，默认异号，允许选项为同号、异号。"
        )
        refreshed = next_due_builder_requirement(
            session,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
        )
        self.assertEqual(refreshed["field"], "implementation_defaults")
        self.assertIn("已更新", refreshed["validation_error"])

    def test_cross_field_contract_rejects_undefined_bands_radius_and_stale_probe(self) -> None:
        session = self._session()
        values = dict(VALID_VALUES)
        values.update(
            {
                "parameter_specifications": (
                    "距离可调：最小0.5 m、最大5.0 m、默认2.0 m、步长0.1 m；"
                    "配置为离散可调选项，默认异号，允许选项为同号、异号。"
                ),
                "model_constants_and_media": (
                    "固定ε₀=8.854e-12 F/m，介质固定为真空且εr=1无量纲。"
                ),
                "measurement_specifications": (
                    "场线形态按连接、弯曲和发散类别作定性定义，不涉及数值计算，单位不适用；"
                    "不使用空间探针，读数与采样不适用。"
                ),
            }
        )
        session.design_context["stage_design_state"] = values
        session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {
            "stage_payload": {"procedure_steps": ["记录近、中、远距离", "保存比较"]}
        }
        session.stage_outputs[Stage.HYPOTHESIS.value] = {
            "stage_payload": {"limiting_cases": ["进入排除半径时停止计算"]}
        }
        values["implementation_defaults"] = format_implementation_defaults(
            build_implementation_defaults(session)
        )
        record_implementation_defaults_approval(session, source="TEST")

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertIn("parameter_specifications", missing)
        self.assertIn("model_constants_and_media", missing)
        self.assertIn("initial_reset_state", missing)

    def test_stale_probe_position_with_plain_zai_is_rejected(self) -> None:
        session = self._session()
        values = {
            **VALID_VALUES,
            "measurement_specifications": (
                "场线形态按连接、弯曲和发散类别作定性定义，不涉及数值计算，单位不适用；"
                "不使用空间探针，读数与采样不适用。"
            ),
            "initial_reset_state": (
                "Initial与Reset均恢复异号电荷、距离1.0 m，探针在两电荷中点并清除快照。"
            ),
        }
        session.design_context["stage_design_state"] = values
        record_implementation_defaults_approval(session, source="TEST")

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertIn("initial_reset_state", missing)

    def test_unrelated_near_middle_far_words_do_not_create_distance_band_loop(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = dict(VALID_VALUES)
        session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {
            "stage_payload": {
                "procedure_steps": [
                    "接近对象后观察中间区域，再比较远场参考图。",
                    "保存比较。",
                ]
            }
        }
        record_implementation_defaults_approval(session, source="TEST")

        missing = {item["field"] for item in missing_builder_requirements(session)}

        self.assertNotIn("parameter_specifications", missing)

    def test_legacy_procedure_change_invalidates_exact_default_contract(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = dict(VALID_VALUES)
        session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value] = {
            "stage_payload": {"procedure_steps": ["建立基准", "记录结果"]}
        }
        record_implementation_defaults_approval(session, source="TEST")
        self.assertTrue(implementation_defaults_approval_valid(session))

        session.stage_outputs[Stage.CONCEPTUAL_PROCEDURE.value]["stage_payload"][
            "procedure_steps"
        ] = ["建立基准", "改变距离", "记录结果"]

        self.assertFalse(implementation_defaults_approval_valid(session))

    def test_stage_clear_blocks_old_builder_value_until_later_emvr_replacement(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = dict(VALID_VALUES)
        session.design_context["emvr_design"] = {
            "field_state": {"expected_results": "旧的预期结果文本。"}
        }
        apply_stage_field_updates(
            session,
            [{"field": "expected_results", "operation": "CLEAR"}],
            stage=Stage.RESULT_INTERPRETATION,
        )
        self.assertEqual(
            builder_requirement_values(session)["expected_results"],
            "",
        )

        session.design_context["emvr_design"]["field_state"]["expected_results"] = (
            "新的、已确认的具体预期结果。"
        )
        reconcile_stage_clear_markers(
            session,
            [
                {
                    "field_id": "expected_results",
                    "operation": "REPLACE",
                    "value": "新的、已确认的具体预期结果。",
                }
            ],
        )
        self.assertEqual(
            builder_requirement_values(session)["expected_results"],
            "新的、已确认的具体预期结果。",
        )

    def test_reentering_same_stage_value_after_emvr_clear_is_not_dropped_as_duplicate(self) -> None:
        session = self._session()
        session.design_context["stage_design_state"] = {}
        update = {
            "field": "expected_results",
            "operation": "REPLACE",
            "value": "同一条具体预期结果。",
        }
        apply_stage_field_updates(
            session,
            [update],
            stage=Stage.RESULT_INTERPRETATION,
        )
        session.design_context["emvr_design"] = {
            "field_state": {},
            "explicitly_cleared_fields": ["expected_results"],
        }

        changed = apply_stage_field_updates(
            session,
            [update],
            stage=Stage.RESULT_INTERPRETATION,
        )

        self.assertEqual(changed, ["expected_results"])
        self.assertNotIn(
            "expected_results",
            session.design_context["emvr_design"]["explicitly_cleared_fields"],
        )
        self.assertEqual(
            builder_requirement_values(session)["expected_results"],
            "同一条具体预期结果。",
        )

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
