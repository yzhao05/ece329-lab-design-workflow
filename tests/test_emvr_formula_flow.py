from __future__ import annotations

import unittest

from ece329_workflow.dialogue_state import UserIntent, resolved_intent
from ece329_workflow.emvr_formula_flow import (
    EMVR_DETAIL_DESIGN,
    EXPERIMENT_METHODS_PRESENTED,
    EXPERIMENT_DIRECTION_REVIEW,
    FORMULA_CANDIDATES_PRESENTED,
    FORMULA_COMPOSITION_REVIEW,
    TOPIC_RECEIVED,
    ensure_emvr_formula_flow,
    formula_support_map_for_selection,
    handle_emvr_formula_turn,
    normalize_topic_analysis,
    score_formula_profiles,
)
from ece329_workflow.engine import WorkflowEngine, _emvr_entry_reference
from ece329_workflow.emvr_design import (
    apply_emvr_field_updates,
    clean_emvr_field_text,
    merge_emvr_structured_requirements,
)
from ece329_workflow.generator import RuleBasedStageGenerator, _emvr_parameter_axis
from ece329_workflow.models import DesignSession, InteractionState, Stage
from ece329_workflow.reporting import (
    build_emvr_task_report,
    effective_emvr_stage_payload,
    effective_experiment_brief,
    stage_report_section,
)


def _topic_analysis() -> dict:
    return {
        "course_domain": "electrostatics",
        "topic_description": "两个带电球靠近时的空间电场变化",
        "mentioned_objects": ["两个带电球"],
        "changed_quantities": ["电荷间距离", "电荷极性配置"],
        "observed_quantities": ["中间区域电场线分布", "零场点位置"],
        "explicit_formula_ids": [],
        "specificity": "PARTIALLY_DEFINED",
        "profile_evidence": [
            {
                "profile_id": "FD02_COULOMB_SUPERPOSITION",
                "course_concept_match": True,
                "variation_match": True,
                "observation_match": True,
                "object_geometry_match": True,
                "boundary_match": True,
                "condition_conflict": False,
            }
        ],
        "confidence": 0.98,
    }


def _formula_intent(action_type: str, content: dict) -> dict:
    formula_action = {"type": action_type, "content": content, "act_id": "test-action"}
    return resolved_intent(
        UserIntent.ANSWER_CURRENT_QUESTION,
        confidence=0.99,
        source="SEMANTIC_TEST",
        semantic_updates={"emvr_formula_actions": [formula_action]},
        dialogue_acts=[
            {
                "type": action_type,
                "target": "emvr_formula_flow",
                "operation": "EXECUTE",
                "content": content,
                "confidence": 0.99,
            }
        ],
        actions_authoritative=True,
    )


class FormulaSemanticGenerator(RuleBasedStageGenerator):
    supports_emvr_formula_flow = True

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        flow = carried_context.get("emvr_formula_flow", {})
        if flow.get("phase") == TOPIC_RECEIVED:
            return _formula_intent("SET_EMVR_TOPIC", _topic_analysis())
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.9,
            source="SEMANTIC_TEST",
        )


class FormulaSelectionOutageGenerator(FormulaSemanticGenerator):
    def resolve_intent(self, session, user_message, pending_action, carried_context):
        flow = carried_context.get("emvr_formula_flow", {})
        if flow.get("phase") == TOPIC_RECEIVED:
            return _formula_intent("SET_EMVR_TOPIC", _topic_analysis())
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )


class FormulaTopicOutageGenerator(RuleBasedStageGenerator):
    supports_emvr_formula_flow = True

    def resolve_intent(self, session, user_message, pending_action, carried_context):
        return resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )


class FormulaQuestionPriorityGenerator(FormulaSemanticGenerator):
    def resolve_intent(self, session, user_message, pending_action, carried_context):
        flow = carried_context.get("emvr_formula_flow", {})
        if flow.get("phase") == TOPIC_RECEIVED:
            return _formula_intent("SET_EMVR_TOPIC", _topic_analysis())
        if "为什么" in user_message:
            candidate = flow["formula_selection"]["candidate_profile_ids"][0]
            return resolved_intent(
                UserIntent.ASK_COURSE_QUESTION,
                confidence=0.99,
                source="SEMANTIC_TEST",
                semantic_updates={
                    "student_questions": ["为什么这组公式适合这个实验？"],
                    "emvr_formula_actions": [
                        {
                            "type": "SELECT_EMVR_FORMULAS",
                            "content": {
                                "primary_profile_ids": [candidate],
                                "supporting_profile_ids": [],
                            },
                        }
                    ],
                },
            )
        return resolved_intent(
            UserIntent.ADVANCE_STAGE,
            confidence=0.99,
            source="SEMANTIC_TEST",
            semantic_updates={"control_actions": ["ADVANCE"]},
        )


class EmvrFormulaFlowTests(unittest.TestCase):
    def test_visible_method_numbers_can_select_a_combined_direction(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个点电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "把公式组合成一个实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )

        review, complete = handle_emvr_formula_turn(
            session,
            "方法1和方法3组合使用：方法1负责正向展示，方法3负责控制变量比较",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
        )

        selected = review.stage_payload["experiment_brief_draft"][
            "selected_experiment_method_ids"
        ]
        self.assertFalse(complete)
        self.assertEqual(
            selected,
            [
                methods.stage_payload["experiment_methods"][0]["method_id"],
                methods.stage_payload["experiment_methods"][2]["method_id"],
            ],
        )

    def test_revision_prefaces_are_removed_from_emvr_values(self) -> None:
        self.assertEqual(
            clean_emvr_field_text(
                "conceptual_objective",
                "概念目标要对应到具体物理内容，概念目标应围绕点电荷场来理解场线重排。",
            ),
            "理解点电荷场中场线重排。",
        )
        self.assertEqual(
            clean_emvr_field_text("vr_interaction_objective", "交互目标更具体一点"),
            "",
        )

    def test_retain_edit_wrappers_are_removed_from_new_and_legacy_emvr_values(self) -> None:
        self.assertEqual(
            clean_emvr_field_text(
                "changed_quantities",
                "主动变化只保留距离和电荷类型（同种/异种）",
            ),
            "距离和电荷类型（同种/异种）",
        )
        self.assertEqual(
            clean_emvr_field_text(
                "observed_quantities",
                "观察只保留场线的合并、扭曲和重排形态",
            ),
            "场线的合并、扭曲和重排形态",
        )
        self.assertEqual(
            clean_emvr_field_text(
                "conceptual_objective",
                "概念目标只保留理解距离和电荷类型如何影响场线重排",
            ),
            "理解距离和电荷类型如何影响场线重排",
        )
        emvr = {
            "field_state": {
                "changed_quantities": ["主动变化只保留距离"],
                "observed_quantities": ["观察只保留场线重排形态"],
                "research_question": "当只保留距离变化时，场线重排形态如何变化？",
            }
        }
        merged = merge_emvr_structured_requirements(emvr)
        self.assertEqual(merged["changed_quantities"], ["距离"])
        self.assertEqual(merged["observed_quantities"], ["场线重排形态"])
        self.assertEqual(
            merged["research_question"],
            "当距离变化时，场线重排形态如何变化？",
        )

    def test_emvr_report_keeps_distinct_objects_with_identical_details(self) -> None:
        session = self._session()
        session.stage_outputs[Stage.CONCEPTUAL_OR_VR_SETUP.value] = {
            "stage_payload": {
                "object_inventory": [
                    {
                        "object_name": name,
                        "category": "物理源对象",
                        "purpose": "产生电场并参与双源叠加比较",
                        "student_interaction": "拖动改变两点电荷间距并切换电荷类型",
                        "physics_or_data_state": "位置、电荷量与正负号",
                        "visual_feedback": "场线随参数实时重排并显示方向箭头",
                        "required": True,
                    }
                    for name in ("点电荷 A", "点电荷 B")
                ]
            }
        }

        report = build_emvr_task_report(session)
        labels = [
            item["label"]
            for section in report["sections"]
            for item in section["items"]
        ]
        self.assertIn("物体 1｜点电荷 A", labels)
        self.assertIn("物体 2｜点电荷 B", labels)

    def test_point_charge_controls_are_lab_specific(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        emvr["selected_primary_formula_ids"] = ["coulomb_point_charge"]
        emvr["field_state"] = {
            "changed_quantities": ["距离", "电荷类型（同种/异种）"],
            "observed_quantities": ["场线合并、扭曲和重排形态"],
            "parameter_specifications": ["距离：0.5 米到 5 米，步长 0.1 米"],
        }
        session.current_stage_index = list(Stage).index(Stage.VARIABLES_AND_CONDITIONS)

        output = RuleBasedStageGenerator().generate(session, "生成变量设计")
        controls = output.stage_payload["controlled_variables"]

        self.assertIn("两个点电荷的电荷量大小", controls)
        self.assertIn("均匀线性介质及介电常数", controls)
        self.assertNotIn("源条件", controls)

    def test_emvr_axis_uses_explicit_visualization_requirements(self) -> None:
        axis = _emvr_parameter_axis(
            {
                "changed_quantities": ["距离；电荷类型（同种/异种）"],
                "observed_quantities": ["场线的合并、扭曲和重排形态"],
                "parameter_specifications": ["距离：最小值0.5米，最大值5米，步长0.1米"],
                "visualization_requirements": [
                    "横轴改为距离，电荷类型用不同颜色曲线区分，纵轴为场线形态指标。"
                ],
            }
        )

        self.assertEqual(axis, ("距离", "米", "场线形态指标", "定性形态指标"))

    def test_numbered_emvr_report_keeps_semantic_items_intact(self) -> None:
        procedure = stage_report_section(
            Stage.CONCEPTUAL_PROCEDURE,
            {
                "procedure_steps": [
                    "核对距离范围；核对同号和异号选项",
                    "加载基准；锁定控制条件",
                    "保存并比较结果",
                ],
            },
        )["items"][0]["value"]
        questions = stage_report_section(
            Stage.RESULT_INTERPRETATION,
            {
                "report_questions": [
                    "同种电荷在远、中、近距离下有什么特征？",
                    "特别是中间区域的场线密度和方向如何变化？",
                    "异种电荷在远、中、近距离下有什么特征？",
                    "特别是中间区域的场线密度和方向如何变化？",
                    "相同距离下两种配置有哪些关键差异？",
                    "这些差异如何用叠加原理解释？",
                ],
            },
        )["items"][0]["value"]

        self.assertEqual(procedure.count("\n"), 2)
        self.assertIn("1. 核对距离范围；核对同号和异号选项", procedure)
        self.assertEqual(questions.count("\n"), 2)
        self.assertIn("这些差异如何用叠加原理解释？", questions)

    def test_point_charge_report_is_specific_formal_and_formula_safe(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        emvr["selected_primary_formula_ids"] = ["coulomb_point_charge"]
        emvr["selected_supporting_formula_ids"] = ["electric_field_superposition"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "我想做一个静电场实验",
            "objects": ["两个点电荷"],
            "operations": ["拖动点电荷并切换电荷类型"],
            "changed_quantities": ["距离", "电荷类型（同种/异种）"],
            "observed_quantities": ["场线的合并、扭曲和重排形态"],
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": ["electric_field_superposition"],
        }
        emvr["field_state"] = {
            "experiment_brief": "我想做一个静电场实验",
            "research_question": "距离和电荷类型如何影响场线形态？",
            "changed_quantities": ["距离", "电荷类型（同种/异种）"],
            "observed_quantities": ["场线的合并、扭曲和重排形态"],
            "parameter_specifications": [
                "距离：最小值0.5米，最大值5米，单位米，建议步长0.1米",
                "电荷类型：允许选项为同种、异种",
            ],
            "visualization_requirements": [
                "横轴改为距离，电荷类型用不同颜色曲线区分，纵轴为场线形态指标。"
            ],
        }
        generator = RuleBasedStageGenerator()
        for stage in (
            Stage.IDEA_BRAINSTORMING,
            Stage.COURSE_MAPPING_AND_DIRECTION,
            Stage.LEARNING_OBJECTIVES,
            Stage.THEORETICAL_FRAMEWORK,
            Stage.HYPOTHESIS,
            Stage.CONCEPTUAL_OR_VR_SETUP,
            Stage.EXPECTED_DATA_VISUALIZATION,
            Stage.DESIGN_VALUE_AND_LIMITATIONS,
        ):
            session.current_stage_index = list(Stage).index(stage)
            session.stage_outputs[stage.value] = generator.generate(
                session, "生成该阶段报告"
            ).to_dict()

        report = build_emvr_task_report(session)
        rendered = "\n".join(
            [report["idea"]]
            + [
                f"{item['label']}：{item['value']}"
                for section in report["sections"]
                for item in section["items"]
            ]
        )

        self.assertNotIn("你", rendered)
        self.assertNotIn("允许学生", rendered)
        self.assertIn("E(r) = Q(r-r_0)/(4*pi*epsilon_0*|r-r_0|^3)", rendered)
        self.assertIn("OnChargeMoved", rendered)
        self.assertIn("1/r^2 奇点", rendered)
        self.assertIn("Lecture 2", rendered)

    def test_legacy_uncertainty_does_not_replace_generated_physical_mechanism(self) -> None:
        session = self._session()
        session.current_stage_index = list(Stage).index(Stage.THEORETICAL_FRAMEWORK)
        session.design_context["stage_design_state"] = {
            "physical_mechanism": "暂时不确定",
        }
        session.stage_outputs[Stage.THEORETICAL_FRAMEWORK.value] = {
            "stage_payload": {
                "physical_mechanism": ["库仑定律", "电场矢量叠加"],
            }
        }

        payload = effective_emvr_stage_payload(session, Stage.THEORETICAL_FRAMEWORK)

        self.assertNotEqual(payload.get("physical_mechanism"), "暂时不确定")
        self.assertIn("库仑定律", str(payload.get("physical_mechanism")))

    def test_generator_uses_emvr_parameters_for_procedure_and_axes(self) -> None:
        session = self._session()
        session.design_context["emvr_design"]["field_state"] = {
            "experiment_brief": "比较两个点电荷在不同距离和极性下的场线变化",
            "research_object": "两个点电荷、可移动测量探针",
            "required_behaviors": ["拖动点电荷并切换同种或异种电荷"],
            "changed_quantities": ["两个点电荷的距离", "电荷类型"],
            "observed_quantities": ["场线弯曲、连接和重排形态"],
            "comparison_cases": ["同种电荷", "异种电荷"],
            "parameter_specifications": [
                "两个点电荷的距离：0.5 米到 5 米，步长 0.1 米",
                "电荷类型：离散选项为同种、异种",
            ],
        }
        generator = RuleBasedStageGenerator()
        session.current_stage_index = list(Stage).index(Stage.CONCEPTUAL_PROCEDURE)
        procedure = generator.generate(session, "整理实验步骤")
        steps_text = "\n".join(procedure.stage_payload["procedure_steps"])
        self.assertIn("0.5 米到 5 米", steps_text)
        self.assertIn("同种电荷、异种电荷", steps_text)
        self.assertIn("场线弯曲、连接和重排形态", steps_text)

        session.current_stage_index = list(Stage).index(
            Stage.EXPECTED_DATA_VISUALIZATION
        )
        visualization = generator.generate(session, "生成显示方案").visualization
        self.assertEqual(visualization["x_axis"]["label"], "两个点电荷的距离")
        self.assertEqual(visualization["x_axis"]["unit"], "米")
        self.assertEqual(
            visualization["y_axis"]["label"],
            "场线弯曲、连接和重排形态",
        )
        session.current_stage_index = list(Stage).index(Stage.CONCEPTUAL_OR_VR_SETUP)
        setup = generator.generate(session, "生成Unity对象清单")
        objects = setup.stage_payload["object_inventory"]
        self.assertTrue(any(item["object_name"] == "点电荷 A" for item in objects))
        self.assertTrue(any(item["object_name"] == "点电荷 B" for item in objects))
        probe = next(item for item in objects if item["object_name"] == "可移动测量探针")
        self.assertEqual(probe["category"], "观察与测量")

        self.assertEqual(
            _emvr_parameter_axis(
                {
                    "changed_quantities": ["激励频率"],
                    "observed_quantities": ["驻波幅度"],
                    "parameter_specifications": ["激励频率：1 kHz 到 10 kHz"],
                }
            )[1],
            "kHz",
        )

    def test_report_repairs_legacy_generic_coulomb_object_and_long_lists(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "点电荷叠加场",
            "summary": "学习目标需要更具体；" * 30,
            "objects": ["研究对象"],
            "operations": ["拖动并比较"],
            "changed_quantities": ["距离"],
            "observed_quantities": ["场线形态"],
            "primary_formula_ids": ["coulomb_point_charge"],
        }
        brief = effective_experiment_brief(session)
        self.assertEqual(brief["objects"], ["两个点电荷"])
        self.assertNotIn("学习目标需要更具体", brief["summary"])

        section = stage_report_section(
            Stage.RESULT_INTERPRETATION,
            {
                "expected_results": [
                    "同种电荷靠近时中间区域场线相斥并弯曲。",
                    "异种电荷靠近时场线连接并重新排列。",
                ],
                "report_questions": [
                    "距离变化如何影响场线形态？",
                    "同种与异种电荷的结果有何不同？",
                    "库仑定律与叠加原理如何解释差异？",
                ],
            },
        )
        rendered = {item["label"]: item["value"] for item in section["items"]}
        self.assertIn("\n2. ", rendered["Lab特有预期结果"])
        self.assertIn("\n3. ", rendered["实验报告问题"])

        scalar_questions = stage_report_section(
            Stage.RESULT_INTERPRETATION,
            {
                "report_questions": (
                    "距离变化如何影响场线？同种与异种电荷有何不同？"
                    "叠加原理如何解释该差异？"
                )
            },
        )
        self.assertIn(
            "\n3. ",
            scalar_questions["items"][0]["value"],
        )

    def test_course_question_pauses_formula_progress_and_preserves_selection(self) -> None:
        engine = WorkflowEngine(generator=FormulaQuestionPriorityGenerator())
        first = engine.create_design(
            "我想研究两个带电球靠近时的电场",
            interaction_state=InteractionState.EMVR_DIRECT,
        )

        answered = engine.process_turn(
            first["design_id"],
            {"message": "为什么这组公式适合这个实验？我也想采用它"},
        )
        stored = engine.store.get(first["design_id"])
        flow = stored.design_context["emvr_design"]["formula_flow"]

        self.assertEqual(flow["phase"], FORMULA_CANDIDATES_PRESENTED)
        self.assertTrue(flow.get("deferred_formula_actions"))
        self.assertTrue(answered["stage_payload"].get("answered_student_questions"))

        continued = engine.process_turn(first["design_id"], {"message": "继续当前选择"})
        flow = engine.store.get(first["design_id"]).design_context["emvr_design"]["formula_flow"]
        self.assertEqual(flow["phase"], FORMULA_COMPOSITION_REVIEW)
        self.assertEqual(
            continued["stage_payload"]["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )

    def _session(self) -> DesignSession:
        session = DesignSession(
            design_id="formula-flow-test",
            interaction_state=InteractionState.EMVR_DIRECT,
            design_context={"idea": {}, "emvr_design": {"field_state": {}}},
        )
        ensure_emvr_formula_flow(session)
        return session

    def test_topic_analysis_accepts_only_catalog_domains_and_formula_ids(self) -> None:
        valid = normalize_topic_analysis(
            {
                **_topic_analysis(),
                "explicit_formula_ids": ["coulomb_point_charge", "invented_formula"],
            }
        )
        invalid = normalize_topic_analysis(
            {**_topic_analysis(), "course_domain": "semiconductor_devices"}
        )

        self.assertEqual(valid["explicit_formula_ids"], ["coulomb_point_charge"])
        self.assertIsNone(invalid)

    def test_broad_electrostatic_topic_shows_formula_families_not_adjacent_theory(self) -> None:
        broad = normalize_topic_analysis(
            {
                **_topic_analysis(),
                "topic_description": "静电场实验",
                "mentioned_objects": [],
                "changed_quantities": [],
                "observed_quantities": [],
                "specificity": "BROAD",
                "profile_evidence": [],
            }
        )
        ranked = score_formula_profiles(broad, limit=4)

        self.assertEqual(
            [item["profile_id"] for item in ranked],
            [
                "FD02_COULOMB_SUPERPOSITION",
                "FD03_GAUSS_FLUX_SYMMETRY",
                "FD04_ELECTROSTATIC_POTENTIAL",
                "FD05_ELECTROSTATIC_BOUNDARY",
            ],
        )
        returned_formula_ids = {
            formula_id
            for item in ranked
            for formula_id in [
                *item["primary_formula_ids"],
                *item["supporting_formula_ids"],
            ]
        }
        self.assertNotIn("lorentz_force", returned_formula_ids)
        self.assertNotIn("ohm_medium", returned_formula_ids)

    def test_candidates_are_not_committed_before_student_confirmation(self) -> None:
        session = self._session()
        output, complete = handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        emvr = session.design_context["emvr_design"]

        self.assertFalse(complete)
        self.assertEqual(output.stage_payload["emvr_formula_phase"], FORMULA_CANDIDATES_PRESENTED)
        self.assertEqual(emvr["formula_flow"]["formula_selection"]["selection_status"], "PENDING")
        self.assertNotIn("selected_primary_formula_ids", emvr)
        self.assertEqual(emvr["field_state"], {})

    def test_engine_recovers_emvr_topic_onboarding_during_semantic_outage(self) -> None:
        engine = WorkflowEngine(generator=FormulaTopicOutageGenerator())
        created = engine.create_design("还没有确定实验方向")

        mode_entry = engine.process_turn(
            created["design_id"],
            {"message": "进入emvr模式"},
        )
        stored = engine.store.get(created["design_id"])
        pending = stored.model_context["dialogue_state"]["pending_action"]

        self.assertEqual(mode_entry["interaction_state"], "EMVR_DIRECT")
        self.assertEqual(
            mode_entry["stage_payload"]["emvr_formula_phase"],
            TOPIC_RECEIVED,
        )
        self.assertEqual(pending["type"], "ANSWER_EMVR_FORMULA_TOPIC")
        self.assertNotIn("semantic_recovery_pending", mode_entry["stage_payload"])

        continued = engine.process_turn(
            created["design_id"],
            {"message": "继续"},
        )
        stored = engine.store.get(created["design_id"])
        pending = stored.model_context["dialogue_state"]["pending_action"]

        self.assertEqual(
            continued["stage_payload"]["emvr_formula_phase"],
            TOPIC_RECEIVED,
        )
        self.assertTrue(continued["stage_payload"]["awaiting_user_design_input"])
        self.assertEqual(pending["type"], "ANSWER_EMVR_FORMULA_TOPIC")
        self.assertNotIn("semantic_recovery_pending", continued["stage_payload"])

        topic = engine.process_turn(
            created["design_id"],
            {"message": "我想做一个有关静电场的实验"},
        )
        stored = engine.store.get(created["design_id"])
        flow = stored.design_context["emvr_design"]["formula_flow"]

        self.assertEqual(
            topic["stage_payload"]["emvr_formula_phase"],
            FORMULA_CANDIDATES_PRESENTED,
        )
        self.assertTrue(topic["stage_payload"]["formula_cards"])
        self.assertEqual(
            topic["stage_payload"]["formula_cards"][0]["profile_id"],
            "FD02_COULOMB_SUPERPOSITION",
        )
        self.assertEqual(
            flow["topic_analysis_source"],
            "CURATED_KNOWLEDGE_FALLBACK",
        )
        self.assertNotIn("semantic_recovery", flow)

    def test_semantic_outage_preserves_formula_turn_and_next_action_recovers(self) -> None:
        session = self._session()
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_OPEN_QUESTION_LOCAL_CLARIFICATION",
        )

        outage, complete = handle_emvr_formula_turn(
            session,
            "我想拖动两个带电球，观察距离减小时中间区域的场线变化",
            failed_intent,
        )
        flow = session.design_context["emvr_design"]["formula_flow"]

        self.assertFalse(complete)
        self.assertTrue(outage.stage_payload["semantic_recovery_pending"])
        self.assertIn("不需要重新开始", outage.assistant_message)
        self.assertNotIn("topic_analysis", flow)
        self.assertEqual(
            flow["semantic_recovery"]["messages"],
            ["我想拖动两个带电球，观察距离减小时中间区域的场线变化"],
        )

        recovered, complete = handle_emvr_formula_turn(
            session,
            "按刚才的想法继续",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )

        self.assertFalse(complete)
        self.assertEqual(
            recovered.stage_payload["emvr_formula_phase"],
            FORMULA_CANDIDATES_PRESENTED,
        )
        self.assertNotIn("semantic_recovery", flow)

    def test_formula_choice_recovers_from_exact_visible_card_during_outage(self) -> None:
        session = self._session()
        handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        flow = session.design_context["emvr_design"]["formula_flow"]
        selected_card = next(
            card
            for card in flow["formula_cards"]
            if card["profile_id"] == "FD02_COULOMB_SUPERPOSITION"
        )
        message = (
            f"我选第一组：{selected_card['title']}。"
            "主要想通过VR看到两个点电荷的场线如何随距离和极性变化"
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        output, complete = handle_emvr_formula_turn(
            session,
            message,
            failed_intent,
        )

        self.assertFalse(complete)
        self.assertEqual(
            flow["formula_selection"]["primary_profile_ids"],
            ["FD02_COULOMB_SUPERPOSITION"],
        )
        self.assertEqual(flow["formula_selection"]["student_rationale"], message)
        self.assertEqual(
            output.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        self.assertNotIn("semantic_recovery", flow)

    def test_composition_choice_recovers_from_visible_option_during_outage(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        handle_emvr_formula_turn(
            session,
            "采用库仑定律和叠加原理",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        output, complete = handle_emvr_formula_turn(
            session,
            "组合公式设计一个完整实验。库仑定律描述单个电荷的场，叠加原理描述多个电荷的合场。",
            failed_intent,
        )

        self.assertFalse(complete)
        self.assertEqual(
            output.stage_payload["emvr_formula_phase"],
            EXPERIMENT_METHODS_PRESENTED,
        )

    def test_method_choice_recovers_from_visible_number_during_outage(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "组合公式设计一个完整实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        output, complete = handle_emvr_formula_turn(
            session,
            "采用方法1",
            failed_intent,
        )
        flow = session.design_context["emvr_design"]["formula_flow"]

        self.assertFalse(complete)
        self.assertEqual(
            output.stage_payload["emvr_formula_phase"],
            EXPERIMENT_DIRECTION_REVIEW,
        )
        self.assertEqual(
            flow["method_selection"]["selected_method_ids"],
            [methods.stage_payload["experiment_methods"][0]["method_id"]],
        )
        self.assertNotIn("semantic_recovery", flow)

    def test_direction_revision_is_applied_during_semantic_outage(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "组合公式设计一个完整实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        handle_emvr_formula_turn(
            session,
            "采用方法1",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=methods.stage_payload["experiment_methods"][0]["option_id"],
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        output, complete = handle_emvr_formula_turn(
            session,
            (
                "主动变化只保留距离和电荷类型（同种/异种），"
                "观察只保留场线的合并、扭曲和重排形态"
            ),
            failed_intent,
        )
        flow = session.design_context["emvr_design"]["formula_flow"]

        self.assertFalse(complete)
        self.assertEqual(
            output.stage_payload["emvr_formula_phase"],
            EXPERIMENT_DIRECTION_REVIEW,
        )
        self.assertEqual(
            flow["experiment_brief"]["changed_quantities"],
            ["距离", "电荷类型（同种/异种）"],
        )
        self.assertEqual(
            flow["experiment_brief"]["observed_quantities"],
            ["场线的合并、扭曲和重排形态"],
        )
        self.assertNotIn("semantic_recovery", flow)

        rule_only_fallback = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.5,
            source="CONSERVATIVE_FALLBACK",
        )
        output, complete = handle_emvr_formula_turn(
            session,
            "主动变化只保留距离，观察只保留场线形态",
            rule_only_fallback,
        )

        self.assertFalse(complete)
        self.assertEqual(
            output.stage_payload["emvr_formula_phase"],
            EXPERIMENT_DIRECTION_REVIEW,
        )
        self.assertEqual(flow["experiment_brief"]["changed_quantities"], ["距离"])
        self.assertEqual(flow["experiment_brief"]["observed_quantities"], ["场线形态"])

        output, complete = handle_emvr_formula_turn(
            session,
            "准确，可以继续",
            rule_only_fallback,
        )

        self.assertTrue(complete)
        self.assertEqual(output.stage_payload["emvr_formula_phase"], EMVR_DETAIL_DESIGN)

    def test_formula_group_number_is_resolved_during_semantic_outage(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想做一个静电场实验",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        output, _ = handle_emvr_formula_turn(
            session,
            "我选第一组，主要研究两个点电荷的场线如何随距离和极性变化",
            failed_intent,
        )
        flow = session.design_context["emvr_design"]["formula_flow"]

        self.assertNotIn("这次课程理解服务没有完成解析", output.assistant_message)
        self.assertEqual(
            flow["formula_selection"]["primary_profile_ids"],
            [cards.stage_payload["formula_cards"][0]["profile_id"]],
        )

    def test_formula_selection_details_are_carried_into_experiment_brief(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        self.assertTrue(
            any(
                card["profile_id"] == "FD02_COULOMB_SUPERPOSITION"
                for card in cards.stage_payload["formula_cards"]
            )
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用库仑定律与叠加原理",
            _formula_intent(
                "SELECT_EMVR_FORMULAS",
                {
                    "primary_profile_ids": ["FD02_COULOMB_SUPERPOSITION"],
                    "objects": ["两个点电荷"],
                    "operations": ["拖拽点电荷改变距离和切换电荷类型"],
                    "changed_quantities": ["电荷间距离", "电荷类型"],
                    "observed_quantities": ["场线的合并、扭曲和重排形态"],
                    "comparison_cases": ["同种电荷", "异种电荷"],
                },
            ),
        )
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "组合成一个完整实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        method = methods.stage_payload["experiment_methods"][0]
        review, _ = handle_emvr_formula_turn(
            session,
            "采用第一种方法",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=method["option_id"],
        )

        brief = review.stage_payload["experiment_brief_draft"]
        self.assertEqual(brief["topic"], _topic_analysis()["topic_description"])
        self.assertEqual(brief["objects"], ["两个点电荷"])
        self.assertEqual(brief["observed_quantities"], ["场线的合并、扭曲和重排形态"])

    def test_formula_outage_keeps_current_cards_clickable(self) -> None:
        session = self._session()
        initial, _ = handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        failed_intent = resolved_intent(
            UserIntent.UNCLEAR,
            confidence=0.62,
            source="SEMANTIC_SERVICE_FALLBACK_LOCAL_CLARIFICATION",
        )

        outage, complete = handle_emvr_formula_turn(
            session,
            "这几组我还需要再比较一下",
            failed_intent,
        )

        self.assertFalse(complete)
        self.assertEqual(
            outage.stage_payload["formula_cards"],
            initial.stage_payload["formula_cards"],
        )
        self.assertTrue(
            all(card.get("option_id") for card in outage.stage_payload["formula_cards"])
        )
        self.assertIn("直接点击", outage.assistant_message)

    def test_engine_does_not_loop_when_visible_formula_choice_hits_outage(self) -> None:
        engine = WorkflowEngine(generator=FormulaSelectionOutageGenerator())
        first = engine.create_design(
            "我想做一个静电场实验",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        selected_card = next(
            card
            for card in first["stage_payload"]["formula_cards"]
            if card["profile_id"] == "FD02_COULOMB_SUPERPOSITION"
        )

        result = engine.process_turn(
            first["design_id"],
            {
                "message": (
                    f"我选第一组：{selected_card['title']}。"
                    "主要想通过VR看到两个点电荷的场线如何随距离和极性变化"
                )
            },
        )

        self.assertEqual(
            result["stage_payload"]["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        self.assertNotIn("请稍后重试刚才的内容", result["assistant_message"])
        stored = engine.store.get(first["design_id"])
        self.assertEqual(
            stored.design_context["emvr_design"]["formula_flow"][
                "formula_selection"
            ]["primary_profile_ids"],
            ["FD02_COULOMB_SUPERPOSITION"],
        )

    def test_effective_brief_preserves_formula_flow_object_enumeration(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        objects = ["两个可拖动带电球", "电场线显示系统", "测量探针"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "两个带电球靠近时的静电场变化",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_experiment_method_ids": ["EMVR-METHOD-test"],
            "objects": objects,
            "operations": ["拖动带电球"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["中间区域电场线"],
            "boundary_conditions": ["静电状态"],
        }
        emvr["field_state"].update(
            {
                "research_object": "、".join(objects),
                "required_behaviors": ["拖动带电球"],
                "changed_quantities": ["电荷间距离"],
                "observed_quantities": ["中间区域电场线"],
                "object_constraints": ["静电状态"],
            }
        )

        brief = effective_experiment_brief(session)

        self.assertEqual(brief["objects"], objects)

    def test_confirmed_formula_drives_generated_methods_and_complete_brief(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        formula_option = cards.stage_payload["formula_cards"][0]["option_id"]
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=formula_option,
        )
        selection = session.design_context["emvr_design"]["formula_flow"]["formula_selection"]
        self.assertEqual(
            set(selection["primary_formula_ids"]),
            {"coulomb_point_charge", "electric_field_superposition"},
        )
        self.assertEqual(selection["supporting_formula_ids"], [])
        self.assertNotIn("potential_volume_charge", selection["primary_formula_ids"])
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "把这些公式组合成一个实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertGreater(len(methods.stage_payload["experiment_methods"]), 3)
        self.assertTrue(all(item["pattern_ids"] for item in methods.stage_payload["experiment_methods"]))
        self.assertTrue(methods.stage_payload["coverage_matrix"]["rows"])
        self.assertEqual(len(methods.stage_payload["coverage_matrix"]["columns"]), 15)
        self.assertTrue(
            all(
                item["method_id"].startswith("EMVR-METHOD-")
                for item in methods.stage_payload["experiment_methods"]
            )
        )
        generated_patterns = {
            pattern_id
            for item in methods.stage_payload["experiment_methods"]
            for pattern_id in item["pattern_ids"]
        }
        self.assertIn("INVERSE_PARAMETER_INFERENCE", generated_patterns)
        self.assertNotIn("覆盖矩阵", methods.assistant_message)
        self.assertNotIn("|公式|", methods.assistant_message)
        self.assertNotIn("简要过程", methods.assistant_message)

        method_option = methods.stage_payload["experiment_methods"][0]["option_id"]
        review, _ = handle_emvr_formula_turn(
            session,
            "采用方法1",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=method_option,
        )
        self.assertEqual(review.stage_payload["emvr_formula_phase"], EXPERIMENT_DIRECTION_REVIEW)
        self.assertNotIn("coulomb_point_charge", review.assistant_message)
        self.assertIn("公式", review.assistant_message)
        self.assertIn("实验过程", review.assistant_message)

        locked, complete = handle_emvr_formula_turn(
            session,
            "确认并继续",
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["ACCEPT"]},
            ),
        )
        emvr = session.design_context["emvr_design"]
        self.assertTrue(complete)
        self.assertEqual(locked.stage_payload["emvr_formula_phase"], EMVR_DETAIL_DESIGN)
        self.assertIn("authoritative_experiment_brief", emvr)
        self.assertEqual(emvr["field_state"]["research_object"], "两个带电球")
        self.assertEqual(emvr["field_state"]["changed_quantities"], ["电荷间距离", "电荷极性配置"])
        support_map = formula_support_map_for_selection(session)
        self.assertEqual(
            {item["formula_id"] for item in support_map},
            {
                *emvr["selected_primary_formula_ids"],
                *emvr["selected_supporting_formula_ids"],
            },
        )
        self.assertTrue(all(item["supports_design_fields"] for item in support_map))

    def test_direction_review_can_reselect_methods_before_locking(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        if composition.stage_payload.get("emvr_formula_phase") == EXPERIMENT_METHODS_PRESENTED:
            methods = composition
        else:
            methods, _ = handle_emvr_formula_turn(
                session,
                "组合成一个实验",
                resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
                selected_option_id="emvr-composition:combined",
            )
        first, second = methods.stage_payload["experiment_methods"][:2]
        handle_emvr_formula_turn(
            session,
            "先看第一种",
            _formula_intent(
                "SELECT_EMVR_EXPERIMENT_METHODS",
                {"selected_method_ids": [first["method_id"]]},
            ),
        )

        revised, complete = handle_emvr_formula_turn(
            session,
            "改看第二种方法",
            _formula_intent(
                "SELECT_EMVR_EXPERIMENT_METHODS",
                {"selected_method_ids": [second["method_id"]]},
            ),
        )

        flow = session.design_context["emvr_design"]["formula_flow"]
        self.assertFalse(complete)
        self.assertEqual(flow["phase"], EXPERIMENT_DIRECTION_REVIEW)
        self.assertEqual(flow["method_selection"]["selected_method_ids"], [second["method_id"]])
        self.assertEqual(
            flow["experiment_brief"]["selected_experiment_method_ids"],
            [second["method_id"]],
        )
        self.assertIn(str(second["title"]), revised.assistant_message)
        self.assertNotIn(str(first["title"]), revised.assistant_message)

        rejected, complete = handle_emvr_formula_turn(
            session,
            "这版不满意，重新选方法",
            resolved_intent(
                UserIntent.REJECT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["REJECT"]},
            ),
        )
        self.assertFalse(complete)
        self.assertEqual(
            rejected.stage_payload["emvr_formula_phase"],
            EXPERIMENT_METHODS_PRESENTED,
        )
        self.assertNotIn("覆盖矩阵", rejected.assistant_message)
        self.assertNotIn("experiment_brief", flow)

    def test_later_field_revision_updates_formula_brief_and_final_report_view(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "采用第一组公式",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=cards.stage_payload["formula_cards"][0]["option_id"],
        )
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"],
            FORMULA_COMPOSITION_REVIEW,
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "组合为一个实验",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:combined",
        )
        handle_emvr_formula_turn(
            session,
            "采用第一种方法",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=methods.stage_payload["experiment_methods"][0]["option_id"],
        )
        handle_emvr_formula_turn(
            session,
            "确认",
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["ACCEPT"]},
            ),
        )

        apply_emvr_field_updates(
            session.design_context["emvr_design"],
            {
                "field_updates": [
                    {
                        "field_id": "experiment_brief",
                        "operation": "REPLACE",
                        "value": "比较电荷距离变化对中间平面电场强度的影响",
                    },
                    {
                        "field_id": "changed_quantities",
                        "operation": "REPLACE",
                        "value": ["两个电荷之间的距离"],
                    },
                    {
                        "field_id": "observed_quantities",
                        "operation": "REPLACE",
                        "value": ["中间平面的电场强度"],
                    },
                ]
            },
        )

        brief = effective_experiment_brief(session)
        report_view = effective_emvr_stage_payload(session, Stage.IDEA_BRAINSTORMING)
        report = build_emvr_task_report(session)
        support_map = formula_support_map_for_selection(session)
        self.assertEqual(
            report["idea"],
            "比较电荷距离变化对中间平面电场强度的影响",
        )
        self.assertEqual(brief["changed_quantities"], ["两个电荷之间的距离"])
        self.assertEqual(brief["observed_quantities"], ["中间平面的电场强度"])
        self.assertEqual(report_view["target_phenomenon"], ["中间平面的电场强度"])
        self.assertTrue(report_view["primary_formulas"])
        self.assertIn("=", report_view["primary_formulas"][0])
        self.assertTrue(
            all("中间平面的电场强度" in item["supports_design_content"] for item in support_map)
        )

    def test_inconsistent_persisted_phase_recovers_instead_of_false_completion(self) -> None:
        session = self._session()
        flow = session.design_context["emvr_design"]["formula_flow"]
        flow["phase"] = EXPERIMENT_METHODS_PRESENTED
        flow["formula_selection"]["primary_formula_ids"] = []
        flow["experiment_methods"] = []

        repaired = ensure_emvr_formula_flow(session)

        self.assertEqual(repaired["phase"], TOPIC_RECEIVED)

    def test_final_hypothesis_view_preserves_distinct_expected_trend(self) -> None:
        session = self._session()
        session.stage_outputs[Stage.HYPOTHESIS.value] = {
            "stage_payload": {
                "research_hypothesis": "距离减小时局部场强增大。",
                "expected_trend": "场强曲线随距离减小而上升。",
                "limiting_cases": ["远距离基准", "近距离模型边界"],
            }
        }
        session.design_context["emvr_design"]["field_state"].update(
            {
                "hypothesis": "距离减小时局部场强增大。",
            }
        )

        report_view = effective_emvr_stage_payload(session, Stage.HYPOTHESIS)

        self.assertEqual(
            report_view["research_hypothesis"],
            "距离减小时局部场强增大。",
        )
        self.assertEqual(
            report_view["expected_trend"],
            "场强曲线随距离减小而上升。",
        )
        self.assertNotEqual(
            report_view["research_hypothesis"],
            report_view["expected_trend"],
        )

    def test_unity_inventory_uses_locked_objects_instead_of_generic_placeholders(self) -> None:
        session = self._session()
        emvr = session.design_context["emvr_design"]
        emvr["authoritative_experiment_brief"] = {
            "topic": "两个带电球的静电场",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "formula_composition_strategy": "SINGLE",
            "selected_experiment_method_ids": ["EMVR-METHOD-test"],
            "selected_experiment_pattern_ids": ["PARAMETER_SWEEP"],
            "objects": ["两个可拖动带电球"],
            "operations": ["用手柄拖动带电球"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["中间区域电场强度"],
            "boundary_conditions": ["均匀线性介质"],
        }
        emvr["field_state"].update(
            {
                "research_object": "两个可拖动带电球",
                "required_behaviors": ["用手柄拖动带电球"],
                "changed_quantities": ["电荷间距离"],
                "observed_quantities": ["中间区域电场强度"],
            }
        )
        session.current_stage_index = list(Stage).index(Stage.CONCEPTUAL_OR_VR_SETUP)

        output = RuleBasedStageGenerator().generate(session, "继续完善Unity对象")
        names = [item["object_name"] for item in output.stage_payload["object_inventory"]]

        self.assertIn("可拖动带电球 A", names)
        self.assertIn("可拖动带电球 B", names)
        self.assertNotIn("学生定义的可交互物理源或带电对象", names)
        self.assertTrue(
            all("中间区域电场强度" in item["visual_feedback"] for item in output.stage_payload["object_inventory"][:1])
        )

    def test_research_question_reference_uses_saved_variables_instead_of_placeholder(self) -> None:
        reference = _emvr_entry_reference(
            Stage.RESEARCH_QUESTION,
            {
                "research_object": "两个点电荷",
                "independent_variable": ["电荷间距离", "电荷类型"],
                "observations": ["场线的合并、扭曲和重排形态"],
            },
        )

        rendered = "；".join(reference)
        self.assertNotIn("当前研究问题", rendered)
        self.assertIn("电荷间距离", rendered)
        self.assertIn("场线的合并、扭曲和重排形态", rendered)

    def test_theory_report_separates_inputs_comparisons_controls_and_baseline(self) -> None:
        session = self._session()
        session.current_stage_index = list(Stage).index(Stage.THEORETICAL_FRAMEWORK)
        session.design_context["emvr_design"]["field_state"].update(
            {
                "research_question": "距离与极性如何影响场线重排？",
                "changed_quantities": ["电荷间距离", "电荷类型"],
                "observed_quantities": ["场线的合并、扭曲和重排形态"],
                "comparison_cases": ["同种电荷", "异种电荷"],
            }
        )

        output = RuleBasedStageGenerator().generate(session, "继续")
        section = stage_report_section(Stage.THEORETICAL_FRAMEWORK, output.stage_payload)
        labels = {item["label"] for item in section["items"]}

        self.assertEqual(output.stage_payload["simulation_inputs"], ["电荷间距离", "电荷类型"])
        self.assertIn("比较情形", labels)
        self.assertIn("保持不变的控制条件", labels)
        self.assertIn("用于比较的基准状态", labels)

    def test_setup_report_uses_concrete_interaction_and_omits_raw_constraint_dump(self) -> None:
        session = self._session()
        session.current_stage_index = list(Stage).index(Stage.CONCEPTUAL_OR_VR_SETUP)
        field_state = session.design_context["emvr_design"]["field_state"]
        field_state.update(
            {
                "research_object": "两个点电荷",
                "required_behaviors": ["拖拽点电荷并切换电荷类型"],
                "changed_quantities": ["电荷间距离", "电荷类型"],
                "observed_quantities": ["场线的合并、扭曲和重排形态"],
                "desktop_interaction_plan": (
                    "桌面端拖拽点电荷改变距离并单击切换类型；"
                    "VR端映射为手柄抓取与按钮切换。"
                ),
                "hidden_object_lifecycle": "无",
            }
        )
        session.design_context["emvr_design"]["authoritative_experiment_brief"] = {
            "objects": ["两个点电荷"],
            "operations": ["拖拽点电荷并切换电荷类型"],
            "changed_quantities": ["电荷间距离", "电荷类型"],
            "observed_quantities": ["场线的合并、扭曲和重排形态"],
        }

        output = RuleBasedStageGenerator().generate(session, "无")
        section = stage_report_section(Stage.CONCEPTUAL_OR_VR_SETUP, output.stage_payload)
        labels = {item["label"] for item in section["items"]}

        self.assertIn("桌面端拖拽点电荷", output.stage_payload["user_role"])
        self.assertNotEqual(output.stage_payload["user_role"], "无")
        self.assertNotIn("学生明确的设计约束", labels)
        source_objects = [
            item for item in output.stage_payload["object_inventory"]
            if item["object_name"] in {"点电荷 A", "点电荷 B"}
        ]
        self.assertEqual(len(source_objects), 2)
        self.assertTrue(
            all("桌面端拖拽点电荷" in item["student_interaction"] for item in source_objects)
        )

    def test_student_can_explicitly_combine_a_second_profile_as_support(self) -> None:
        session = self._session()
        handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        composition, _ = handle_emvr_formula_turn(
            session,
            "以库仑定律为主，并用高斯定律辅助比较",
            _formula_intent(
                "SELECT_EMVR_FORMULAS",
                {
                    "primary_profile_ids": ["FD02_COULOMB_SUPERPOSITION"],
                    "supporting_profile_ids": ["FD03_GAUSS_FLUX_SYMMETRY"],
                    "student_rationale": "用闭合面通量辅助比较",
                },
            ),
        )

        selection = session.design_context["emvr_design"]["formula_flow"]["formula_selection"]
        self.assertEqual(
            composition.stage_payload["emvr_formula_phase"], FORMULA_COMPOSITION_REVIEW
        )
        self.assertEqual(
            set(selection["primary_formula_ids"]),
            {"coulomb_point_charge", "electric_field_superposition"},
        )
        self.assertEqual(
            set(selection["supporting_formula_ids"]),
            {"gauss_integral", "gauss_differential"},
        )
        methods, _ = handle_emvr_formula_turn(
            session,
            "逐个公式设计后组合",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id="emvr-composition:separate_then_combine",
        )
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertTrue(
            all(
                item["primary_formula_ids"]
                for item in methods.stage_payload["experiment_methods"]
            )
        )
        matrix_cells = {
            (row["formula_id"], pattern_id)
            for row in methods.stage_payload["coverage_matrix"]["rows"]
            for pattern_id in row["applicable_pattern_ids"]
        }
        generated_cells = {
            (assignment["formula_id"], assignment["pattern_id"])
            for method in methods.stage_payload["experiment_methods"]
            for assignment in method["formula_pattern_assignments"]
        }
        # The internal matrix remains exhaustive, while the student-facing
        # method catalog is capped to avoid an overwhelming list.
        self.assertLessEqual(generated_cells, matrix_cells)
        self.assertLessEqual(len(methods.stage_payload["experiment_methods"]), 6)

    def test_formula_choice_and_composition_can_be_handled_in_one_long_turn(self) -> None:
        session = self._session()
        handle_emvr_formula_turn(
            session,
            "我想研究两个电荷",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        intent = resolved_intent(
            UserIntent.ANSWER_CURRENT_QUESTION,
            semantic_updates={
                "emvr_formula_actions": [
                    {
                        "type": "SELECT_EMVR_FORMULAS",
                        "content": {
                            "primary_profile_ids": ["FD02_COULOMB_SUPERPOSITION"],
                            "supporting_profile_ids": [],
                        },
                    },
                    {
                        "type": "SET_EMVR_FORMULA_COMPOSITION",
                        "content": {"strategy": "SEPARATE_THEN_COMBINE"},
                    },
                ]
            },
        )

        methods, _ = handle_emvr_formula_turn(
            session,
            "采用库仑与叠加公式，并先分别设计小实验再组合",
            intent,
        )

        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertEqual(
            session.design_context["emvr_design"]["formula_flow"]["formula_composition"]["strategy"],
            "SEPARATE_THEN_COMBINE",
        )

    def test_formula_cards_label_supporting_formulas_as_optional(self) -> None:
        session = self._session()
        output, _ = handle_emvr_formula_turn(
            session,
            "我想研究静电场",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )

        self.assertIn("可选辅助", output.assistant_message)

    def test_single_formula_skips_composition_question_and_generates_methods(self) -> None:
        session = self._session()
        cards, _ = handle_emvr_formula_turn(
            session,
            "我想研究静电边界",
            _formula_intent("SET_EMVR_TOPIC", _topic_analysis()),
        )
        boundary_card = next(
            item
            for item in cards.stage_payload["formula_cards"]
            if item["profile_id"] == "FD05_ELECTROSTATIC_BOUNDARY"
        )

        methods, _ = handle_emvr_formula_turn(
            session,
            "采用静电边界条件",
            resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION),
            selected_option_id=boundary_card["option_id"],
        )

        flow = session.design_context["emvr_design"]["formula_flow"]
        self.assertEqual(
            methods.stage_payload["emvr_formula_phase"], EXPERIMENT_METHODS_PRESENTED
        )
        self.assertEqual(flow["formula_composition"]["strategy"], "SINGLE")
        self.assertTrue(methods.stage_payload["experiment_methods"])

    def test_legacy_fixed_scene_phase_is_migrated_to_pattern_generation(self) -> None:
        session = self._session()
        flow = session.design_context["emvr_design"]["formula_flow"]
        flow["phase"] = "FORMULA_SCENES_PRESENTED"
        flow["formula_scenes"] = [{"scene_id": "ECE329-S001"}]

        migrated = ensure_emvr_formula_flow(session)

        # The retired scene state did not contain a valid confirmed formula
        # selection, so recovery returns to the topic/formula boundary instead
        # of falsely advancing with an empty design.
        self.assertEqual(migrated["phase"], TOPIC_RECEIVED)
        self.assertNotIn("formula_scenes", migrated)

    def test_review_revision_changes_only_named_brief_field(self) -> None:
        session = self._session()
        flow = ensure_emvr_formula_flow(session)
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        flow["experiment_brief"] = {
            "topic": "原研究方向",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_scene_ids": ["ECE329-S004"],
            "objects": ["两个点电荷"],
            "operations": ["拖动其中一个电荷"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["电场线形状"],
            "boundary_conditions": ["点电荷近似"],
        }
        output, complete = handle_emvr_formula_turn(
            session,
            "观察量改成中间区域场强，其他不变",
            _formula_intent(
                "REVISE_EMVR_DIRECTION",
                {"brief_updates": {"observed_quantities": ["中间区域场强"]}},
            ),
        )

        self.assertFalse(complete)
        self.assertEqual(flow["experiment_brief"]["observed_quantities"], ["中间区域场强"])
        self.assertEqual(flow["experiment_brief"]["objects"], ["两个点电荷"])
        self.assertEqual(flow["experiment_brief"]["changed_quantities"], ["电荷间距离"])
        self.assertIn("只调整", output.assistant_message)

    def test_review_merge_preserves_existing_items(self) -> None:
        session = self._session()
        flow = ensure_emvr_formula_flow(session)
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        flow["experiment_brief"] = {
            "topic": "点电荷空间场",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": [],
            "selected_scene_ids": ["ECE329-S004"],
            "objects": ["两个点电荷"],
            "operations": ["拖动点电荷"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["电场线形状"],
            "boundary_conditions": ["点电荷近似"],
        }

        handle_emvr_formula_turn(
            session,
            "再加入零场点位置，原来的保留",
            _formula_intent(
                "REVISE_EMVR_DIRECTION",
                {
                    "brief_updates": {
                        "observed_quantities": {
                            "operation": "MERGE",
                            "value": ["零场点位置"],
                        }
                    }
                },
            ),
        )

        self.assertEqual(
            flow["experiment_brief"]["observed_quantities"],
            ["电场线形状", "零场点位置"],
        )

    def test_review_applies_comparison_and_observation_edits_independently(self) -> None:
        session = self._session()
        flow = ensure_emvr_formula_flow(session)
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        flow["experiment_brief"] = {
            "topic": "两个点电荷靠近时的空间电场",
            "primary_formula_ids": ["coulomb_point_charge"],
            "supporting_formula_ids": ["electric_field_superposition"],
            "selected_experiment_method_ids": ["EMVR-METHOD-test"],
            "selected_experiment_pattern_ids": ["CONTROLLED_COMPARISON"],
            "objects": ["两个点电荷"],
            "operations": ["连续改变两电荷间距"],
            "changed_quantities": ["电荷间距离"],
            "observed_quantities": ["电场线形状", "零场点和对称性"],
            "comparison_cases": [],
            "boundary_conditions": ["点电荷近似"],
        }

        output, complete = handle_emvr_formula_turn(
            session,
            "同时调整基础比较和观察量",
            _formula_intent(
                "REVISE_EMVR_DIRECTION",
                {
                    "brief_updates": {
                        "comparison_cases": {
                            "operation": "REPLACE",
                            "value": ["同种电荷", "异种电荷"],
                        },
                        "observed_quantities": {
                            "operation": "REPLACE",
                            "value": ["场线形状、合并、扭曲和重排形态"],
                        },
                    }
                },
            ),
        )

        self.assertFalse(complete)
        self.assertEqual(
            flow["experiment_brief"]["comparison_cases"],
            ["同种电荷", "异种电荷"],
        )
        self.assertEqual(
            flow["experiment_brief"]["observed_quantities"],
            ["场线形状、合并、扭曲和重排形态"],
        )
        self.assertNotIn("零场点", output.assistant_message)

        _, complete = handle_emvr_formula_turn(
            session,
            "确认方向",
            resolved_intent(
                UserIntent.ACCEPT_PREVIOUS_PROPOSAL,
                semantic_updates={"control_actions": ["ACCEPT"]},
            ),
        )
        self.assertTrue(complete)
        emvr = session.design_context["emvr_design"]
        self.assertEqual(
            emvr["field_state"]["comparison_cases"],
            ["同种电荷", "异种电荷"],
        )
        self.assertEqual(
            effective_experiment_brief(session)["comparison_cases"],
            ["同种电荷", "异种电荷"],
        )

    def test_engine_advances_only_after_formula_scene_and_direction_lock(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        first = engine.create_design(
            "我想研究两个带电球靠近时的电场",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        self.assertEqual(first["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(first["stage_payload"]["emvr_formula_phase"], FORMULA_CANDIDATES_PRESENTED)

        formula = first["stage_payload"]["formula_cards"][0]
        second = engine.process_turn(
            first["design_id"],
            {"message": "采用这组公式", "selected_option_id": formula["option_id"]},
        )
        self.assertEqual(
            second["stage_payload"]["emvr_formula_phase"], FORMULA_COMPOSITION_REVIEW
        )
        third = engine.process_turn(
            first["design_id"],
            {
                "message": "组合成一个完整实验",
                "selected_option_id": "emvr-composition:combined",
            },
        )
        method = third["stage_payload"]["experiment_methods"][0]
        review = engine.process_turn(
            first["design_id"],
            {"message": "采用方法1", "selected_option_id": method["option_id"]},
        )
        self.assertEqual(review["current_stage"], Stage.IDEA_BRAINSTORMING.value)

        fourth = engine.process_turn(
            first["design_id"],
            {"message": "确认方向", "complete_stage": True},
        )
        self.assertEqual(fourth["current_stage"], Stage.COURSE_MAPPING_AND_DIRECTION.value)
        self.assertIn(Stage.IDEA_BRAINSTORMING.value, fourth["completed_stages"] if "completed_stages" in fourth else engine.get_design(first["design_id"])["completed_stages"])

    def test_guided_mode_does_not_initialize_or_render_formula_flow(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        result = engine.create_design("我想研究静电场")
        stored = engine.store.get(result["design_id"])

        self.assertEqual(result["interaction_state"], InteractionState.GUIDED_DESIGN.value)
        self.assertNotIn("formula_cards", result["stage_payload"])
        self.assertNotIn("formula_scenes", result["stage_payload"])
        self.assertNotIn("emvr_design", stored.design_context)

    def test_mode_handoff_keeps_topic_meaning_without_committing_a_brief(self) -> None:
        engine = WorkflowEngine(generator=FormulaSemanticGenerator())
        guided = engine.create_design("我想比较两个电荷靠近时的电场线")
        switched = engine.process_turn(guided["design_id"], {"message": "进入EMVR模式"})
        emvr = engine.store.get(guided["design_id"]).design_context["emvr_design"]

        self.assertEqual(switched["interaction_state"], InteractionState.EMVR_DIRECT.value)
        self.assertEqual(switched["stage_payload"]["emvr_formula_phase"], TOPIC_RECEIVED)
        self.assertIn("两个电荷", emvr["formula_flow"]["topic_seed"])
        self.assertNotIn("experiment_brief", emvr)
        self.assertEqual(emvr["field_state"], {})

    def test_new_topic_from_later_stage_reenters_formula_flow_without_legacy_write(self) -> None:
        class NewTopicGenerator(FormulaSemanticGenerator):
            def resolve_intent(self, session, user_message, pending_action, carried_context):
                if user_message == "换成研究静电边界":
                    return resolved_intent(
                        UserIntent.NEW_TOPIC,
                        confidence=0.99,
                        source="SEMANTIC_TEST",
                        dialogue_acts=[
                            {
                                "type": "NEW_TOPIC_CONTENT",
                                "target": "research_topic",
                                "operation": "REPLACE",
                                "content": "研究静电边界",
                                "confidence": 0.99,
                            }
                        ],
                        actions_authoritative=True,
                        preserve_current_design=False,
                    )
                return super().resolve_intent(
                    session, user_message, pending_action, carried_context
                )

        engine = WorkflowEngine(generator=NewTopicGenerator())
        first = engine.create_design(
            "两个点电荷靠近时的电场",
            interaction_state=InteractionState.EMVR_DIRECT,
        )
        formula = first["stage_payload"]["formula_cards"][0]
        scenes = engine.process_turn(
            first["design_id"],
            {"message": "采用公式", "selected_option_id": formula["option_id"]},
        )
        methods = engine.process_turn(
            first["design_id"],
            {
                "message": "组合公式",
                "selected_option_id": "emvr-composition:combined",
            },
        )
        method = methods["stage_payload"]["experiment_methods"][0]
        engine.process_turn(
            first["design_id"],
            {"message": "采用方法", "selected_option_id": method["option_id"]},
        )
        engine.process_turn(
            first["design_id"], {"message": "确认", "complete_stage": True}
        )

        changed = engine.process_turn(first["design_id"], {"message": "换成研究静电边界"})
        emvr = engine.store.get(first["design_id"]).design_context["emvr_design"]
        self.assertEqual(changed["current_stage"], Stage.IDEA_BRAINSTORMING.value)
        self.assertEqual(changed["stage_payload"]["emvr_formula_phase"], TOPIC_RECEIVED)
        self.assertEqual(emvr["formula_flow"]["topic_seed"], "研究静电边界")
        self.assertNotIn("experiment_brief", emvr)
        self.assertEqual(emvr["field_state"], {})


if __name__ == "__main__":
    unittest.main()
