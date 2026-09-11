from copy import deepcopy
from io import BytesIO
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from ece329_workflow.builder_input import _field, _text, render_builder_review_pdf, _student_task_contracts, _contract_parts
from ece329_workflow.builder_portability import (
    PACK_NAME, ROOT_DISCOVERY, embed_supplied_references,
    resolve_local_builder_pack, validate_portable_content,
)
from ece329_workflow.builder_requirements import _defines_named_distance_bands, _cross_field_validation_error, builder_requirement_values, missing_builder_requirements
from ece329_workflow.models import DesignSession, InteractionState


@pytest.mark.parametrize("legacy", [None, r"D:\workshop\EMVR_Blind_BuilderPack", "/home/alice/EMVR_Blind_BuilderPack", PACK_NAME])
def test_legacy_or_absent_machine_path_never_becomes_student_requirement(legacy):
    session = DesignSession(design_id="portable", interaction_state=InteractionState.EMVR_DIRECT)
    session.design_context["stage_design_state"] = {"builder_workspace_absolute_path": legacy}
    before = deepcopy(session.design_context)
    assert builder_requirement_values(session)["builder_workspace_absolute_path"] == PACK_NAME
    assert "builder_workspace_absolute_path" not in {row["field"] for row in missing_builder_requirements(session)}
    assert session.design_context == before


def make_pack(parent):
    root = parent / PACK_NAME
    for name in ("Tools/labflow/labflow.py", "LabSpecs/templates/lab-brief.template.yaml", "UnityProject/ProjectSettings/ProjectVersion.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    return root


@pytest.fixture
def portable_workspace():
    # Python 3.13's Windows mode=0700 (pytest tmp_path) can exclude the
    # restricted test token. Use a normal workspace directory, still isolated.
    base = (Path(__file__).resolve().parents[1] / "build/portability-tests").resolve()
    directory = base / uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        resolved = directory.resolve()
        if resolved.is_relative_to(base) and resolved != base:
            shutil.rmtree(resolved)


def test_discovery_works_after_moving_checkout_and_from_inside(portable_workspace):
    tmp_path = portable_workspace
    first = make_pack(tmp_path / "first")
    second = make_pack(tmp_path / "another-computer")
    assert resolve_local_builder_pack(first / "UnityProject/ProjectSettings") == first
    assert resolve_local_builder_pack(second.parent) == second
    with pytest.raises(ValueError):
        resolve_local_builder_pack(tmp_path)  # No recursive/sibling search.


def test_discovery_requires_real_pack_sentinels(portable_workspace):
    tmp_path = portable_workspace
    root = tmp_path / PACK_NAME
    root.mkdir()
    with pytest.raises(ValueError):
        resolve_local_builder_pack(root)


@pytest.mark.parametrize('offset', [881, 894, 900, 905, 1800])
def test_contract_pagination_never_detaches_relative_path_root(offset):
    content = '说明。' * (offset // 3) + ' ' * (offset % 3) + 'Assets/Scenes/example.unity；本 PDF 内嵌参考 REF_01。'
    parts = _contract_parts(content)
    assert ''.join(parts) == content
    assert any('Assets/Scenes/example.unity' in part for part in parts)
    validate_portable_content(parts)
    with pytest.raises(ValueError):
        validate_portable_content(_contract_parts(content + ' ../outside.txt'))


@pytest.mark.parametrize("value", [
    r"D:\another-user\lecture.pdf", "file:///home/user/source.pdf", r"\\server\share\brief.md",
    "/home/user/source.pdf", "//server/share/source.pdf", "/课程/讲义.pdf", "~/private.pdf", "../lecture.md", "Assets/../../outside.txt",
    "notes/%2e%2e/source.pdf", "lecture_notes/chapter2.pdf", "workflow/private/requirements.yaml",
])
def test_boundary_checks_all_exported_layers(value):
    with pytest.raises(ValueError):
        validate_portable_content({"appendix": [("reference", [{"note": value}])]})


@pytest.mark.parametrize("value", [ROOT_DISCOVERY, "LabSpecs/<lab_id>/brief.yaml", "UnityProject/Packages/manifest.json",
    "E_i(r) = Q_i*(r-r_i)/(4*pi*epsilon_0*|r-r_i|^3)", "N/C; dr/ds; Common/XRI; file: dependencies",
    "ApprovedAssets/EMVRRoom/Prefabs/Room_Big_Part_01.prefab"])
def test_boundary_accepts_pack_templates_and_physics_notation(value):
    validate_portable_content(value)


def test_external_reference_requires_actual_content_and_is_copied_without_reading_path():
    source = r"Z:\unavailable\lecture.pdf"
    payload, appendix = embed_supplied_references({"rule": f"source: {source}"}, [
        {"source_path": source, "title": "Lecture 2, pp.22-24", "content": "E_total = E_A + E_B; static charges in vacuum."},
    ])
    assert source not in str(payload) + str(appendix)
    assert "E_total = E_A + E_B" in appendix[0]["value"]
    assert "REF_01" in payload["rule"]
    validate_portable_content([payload, appendix])
    with pytest.raises(ValueError, match="copied content"):
        embed_supplied_references({}, [{"source_path": source}])
    with pytest.raises(ValueError, match="list"):
        embed_supplied_references({}, {"source_path": source})


def test_value_export_preserves_zero_false_siblings_and_deep_parameters():
    value = {"name": "model", "value": 0, "enabled": False, "details": {"inner": [{"controls": {"default": 0, "unit": "m"}}]}}
    rendered = _text(value)
    assert all(part in rendered for part in ("value=0", "enabled=false", "default=0", "unit=m"))
    assert _field("test", False)["value"] == "false"
    assert _field("test", 0)["value"] == "0"


def test_near_middle_far_labels_cannot_borrow_other_numbers():
    assert not _defines_named_distance_bands("距离最小0.5 m、最大5 m、默认2 m、步长0.1 m；近/中/远")
    assert _defines_named_distance_bands("近/中/远分别0.5 m、2 m、5 m")


def test_missing_numeric_precision_cannot_hide_behind_step_size():
    session = DesignSession(design_id="numeric", interaction_state=InteractionState.EMVR_DIRECT)
    field = "numerical_model_specifications"
    value = "RK4积分；误差/精度：固定步长0.05米；最多500步。"
    assert "容差" in _cross_field_validation_error(session, field, {field: value})
    assert _cross_field_validation_error(session, field, {field: value + "位置误差容差0.01 m。"}) is None
    assert _cross_field_validation_error(session, field, {field: "直接解析求值；无需数值积分；没有轨迹。"}) is None


def test_charge_magnitudes_do_not_imply_signed_identity():
    session = DesignSession(design_id="signs", interaction_state=InteractionState.EMVR_DIRECT)
    field = "model_constants_and_media"
    values = {field: "两个点电荷的电荷量绝对值均为1e-9 C", "initial_reset_state": "初始2m同号"}
    assert "带符号" in _cross_field_validation_error(session, field, values)
    values["initial_reset_state"] = "A=+1 nC，A=-1 nC"
    assert _cross_field_validation_error(session, field, values)
    values["initial_reset_state"] = "A=+1 nC，B=+1 nC；切换异号时B=-1 nC"
    assert _cross_field_validation_error(session, field, values) is None


def test_visual_blank_area_is_not_a_forced_physical_result():
    session = DesignSession(design_id="science", interaction_state=InteractionState.EMVR_DIRECT)
    field = "expected_results"
    assert _cross_field_validation_error(session, field, {field: "同号靠近时场线的空白区显著扩大。"})
    assert _cross_field_validation_error(session, field, {field: "不强制场线空白区显著扩大；按公式观察和解释。"}) is None


def test_baseline_and_compare_state_conditions_are_distinct():
    rows = _student_task_contracts(["加载基准并记录快照", "并排比较所选快照"], "定性比较")
    assert rows[0]["exit_state"] == "CAPTURED"
    assert "preserve saved snapshots" in rows[0]["unity_response"]
    assert "at least two valid compatible snapshots" in rows[1]["unity_response"]


def test_long_pdf_values_repeat_ids_and_keep_tail_without_blank_cells():
    # Real PDF regression: label every continuation, keep the last constraint,
    # and prove zero/false/N/A survive the artifact boundary.
    pdfplumber = pytest.importorskip("pdfplumber")
    content = render_builder_review_pdf({
        "title": "Portable review", "purpose": "Rendering regression", "source_design_id": "fixture",
        "target_gate": "Gate 1 input", "template_reference": "LabSpecs/templates/lab-brief.template.yaml",
    }, [("Field review", [
        _field("long_value", "完整约束内容。" * 700 + "TAIL_MUST_SURVIVE"),
        _field("zero", 0), _field("flag", False),
        _field("probe", "not-applicable: qualitative global morphology", status="not-applicable"),
    ])], [])
    with pdfplumber.open(BytesIO(content)) as pdf:
        text = "".join(page.extract_text() or "" for page in pdf.pages)
        assert len(pdf.pages) > 1 and "TAIL_MUST_SURVIVE" in text
        assert "part 2/" in text and "false" in text and "not-applicable" in text
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    assert all(cell and cell.strip() for cell in row), row
