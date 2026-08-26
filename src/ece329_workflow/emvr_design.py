from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .knowledge_base import KNOWLEDGE
from .models import Stage


# These are physical roles in an EMVR experiment, not words to search for in
# the student's message.  The semantic resolver chooses the roles from the
# complete conversational context; the deterministic layer then maps those
# roles to the smallest useful set of course equations.
EMVR_THEORY_RELATIONS: dict[str, dict[str, Any]] = {
    "ELECTRIC_SOURCE_FIELD": {
        "label": "电荷源与静电场",
        "formula_ids": ["coulomb_point_charge"],
    },
    "FIELD_SUPERPOSITION": {
        "label": "多个场源的矢量叠加",
        "formula_ids": ["electric_field_superposition"],
    },
    "ELECTRIC_FLUX": {
        "label": "电通量与包围电荷",
        "formula_ids": ["gauss_integral", "gauss_differential"],
    },
    "ELECTROSTATIC_POTENTIAL": {
        "label": "静电场与电势",
        "formula_ids": ["electrostatic_potential_gradient", "potential_difference"],
    },
    "ELECTROSTATIC_BOUNDARY": {
        "label": "静电边界条件",
        "formula_ids": ["electrostatic_boundary"],
    },
    "DIELECTRIC_RESPONSE": {
        "label": "介质极化与电位移",
        "formula_ids": ["electric_displacement", "linear_dielectric"],
    },
    "ELECTROSTATIC_FIELD_SOLUTION": {
        "label": "无源区或有源区的静电场求解",
        "formula_ids": ["laplace_equation", "poisson_equation"],
    },
    "CAPACITANCE": {
        "label": "电容、电荷与电势差",
        "formula_ids": ["capacitance_definition", "parallel_plate_capacitance"],
    },
    "OHMIC_CONDUCTION": {
        "label": "导电介质中的电流响应",
        "formula_ids": ["ohm_law_density"],
    },
    "CHARGE_RELAXATION": {
        "label": "导电介质中的自由电荷弛豫",
        "formula_ids": ["charge_relaxation"],
    },
    "CHARGED_PARTICLE_FORCE": {
        "label": "带电粒子在电磁场中的受力与运动",
        "formula_ids": ["lorentz_force"],
    },
    "MAGNETIC_SOURCE_FIELD": {
        "label": "稳恒电流产生的磁场",
        "formula_ids": ["biot_savart", "ampere_integral"],
    },
    "ELECTROMAGNETIC_INDUCTION": {
        "label": "磁通变化与感应电动势",
        "formula_ids": ["faraday_differential", "faraday_generalized_emf"],
    },
    "INDUCTANCE": {
        "label": "磁通、电流与电感",
        "formula_ids": ["inductance_definition", "magnetic_energy"],
    },
    "MAXWELL_FIELD_COUPLING": {
        "label": "时变电场与磁场的耦合",
        "formula_ids": ["ampere_maxwell", "material_maxwell_relations"],
    },
    "WAVE_PROPAGATION": {
        "label": "电磁波传播速度、场关系与波阻抗",
        "formula_ids": ["wave_equation", "tem_traveling_wave", "wave_speed_impedance"],
    },
    "WAVE_ENERGY_POWER": {
        "label": "电磁能量流与平均功率",
        "formula_ids": ["poynting_vector", "average_poynting"],
    },
    "WAVE_POLARIZATION": {
        "label": "电磁波偏振",
        "formula_ids": ["circular_polarization"],
    },
    "WAVE_INTERFACE_REFLECTION": {
        "label": "介质界面的波反射与透射",
        "formula_ids": ["wave_reflection_transmission"],
    },
    "CONDUCTOR_WAVE_LOSS": {
        "label": "导电介质中的衰减与趋肤效应",
        "formula_ids": ["conducting_medium_gamma_eta", "skin_depth"],
    },
    "TRANSMISSION_LINE_PROPAGATION": {
        "label": "传输线上的电压电流传播",
        "formula_ids": ["telegrapher_lossless", "tl_speed_impedance"],
    },
    "TRANSMISSION_LINE_REFLECTION": {
        "label": "传输线负载反射与驻波",
        "formula_ids": ["tl_load_source_reflection", "load_reflection_phasor", "vswr"],
    },
    "TRANSMISSION_LINE_RESONANCE": {
        "label": "传输线边界与共振",
        "formula_ids": ["same_boundary_resonance", "mixed_boundary_resonance"],
    },
    "TRANSMISSION_LINE_MATCHING": {
        "label": "传输线阻抗变换与匹配",
        "formula_ids": ["quarter_wave_transformer", "quarter_wave_matching"],
    },
    "TRANSMISSION_LINE_LOSS": {
        "label": "有损传输线传播",
        "formula_ids": ["lossy_telegrapher", "lossy_line_gamma_z0"],
    },
}

EMVR_THEORY_RELATION_IDS = frozenset(EMVR_THEORY_RELATIONS)

# Semantic design fields used by the intent resolver and state machine.  They
# are deliberately independent of any wording in the student's message.
EMVR_SCALAR_FIELDS = frozenset(
    {"direction_summary", "research_summary", "research_question", "hypothesis"}
)
EMVR_LIST_FIELDS = frozenset(
    {
        "learning_objectives",
        "changed_quantities",
        "observed_quantities",
        "comparison_cases",
        "required_behaviors",
        "object_constraints",
        "procedure_steps",
        "visualization_requirements",
        "limitations",
    }
)
EMVR_EDITABLE_FIELDS = EMVR_SCALAR_FIELDS | EMVR_LIST_FIELDS


def _nonempty_field_value(field_id: str, value: Any) -> str | list[str] | None:
    if field_id in EMVR_SCALAR_FIELDS:
        text = str(value).strip()[:1600] if isinstance(value, str) else ""
        return text or None
    values = value if isinstance(value, list) else [value]
    result = list(
        dict.fromkeys(
            str(item).strip()[:800]
            for item in values
            if isinstance(item, str) and item.strip()
        )
    )[:20]
    return result or None


def apply_emvr_field_updates(
    emvr_design: dict[str, Any],
    structured_update: dict[str, Any],
) -> None:
    """Apply each semantic field edit independently and preserve siblings."""

    field_state = emvr_design.setdefault("field_state", {})
    if not isinstance(field_state, dict):
        field_state = {}
        emvr_design["field_state"] = field_state

    raw_edits = structured_update.get("field_updates", [])
    edits = raw_edits if isinstance(raw_edits, list) else []
    # Snapshot fields keep older clients compatible on ordinary answer turns.
    # Once explicit edits exist, however, only those targeted operations may
    # mutate field_state.  A model-provided stale snapshot must not overwrite
    # an unrelated field that the student did not ask to change.
    if not edits:
        for field_id in EMVR_EDITABLE_FIELDS:
            if field_id not in structured_update:
                continue
            value = _nonempty_field_value(field_id, structured_update.get(field_id))
            if value is not None:
                field_state[field_id] = deepcopy(value)

    for edit in edits:
        if not isinstance(edit, dict):
            continue
        field_id = str(edit.get("field_id") or "")
        operation = str(edit.get("operation") or "").upper()
        if field_id not in EMVR_EDITABLE_FIELDS:
            continue
        value = _nonempty_field_value(field_id, edit.get("value"))
        if operation == "CLEAR":
            field_state.pop(field_id, None)
        elif operation == "REPLACE" and value is not None:
            field_state[field_id] = deepcopy(value)
        elif operation == "MERGE" and value is not None:
            if field_id in EMVR_SCALAR_FIELDS:
                prior = str(field_state.get(field_id) or "").strip()
                addition = str(value).strip()
                field_state[field_id] = "；".join(
                    dict.fromkeys(item for item in (prior, addition) if item)
                )
            else:
                prior_values = field_state.get(field_id, [])
                prior_values = prior_values if isinstance(prior_values, list) else []
                field_state[field_id] = list(dict.fromkeys([*prior_values, *value]))


def merge_emvr_structured_requirements(emvr_design: Any) -> dict[str, Any]:
    """Merge per-stage semantic readings, with later revisions authoritative."""

    by_stage = (
        emvr_design.get("structured_requirements", {})
        if isinstance(emvr_design, dict)
        else {}
    )
    if not isinstance(by_stage, dict):
        return {}
    merged: dict[str, Any] = {}
    for stage in Stage:
        update = by_stage.get(stage.value)
        if not isinstance(update, dict):
            continue
        for key, value in update.items():
            if key == "field_updates":
                continue
            if value not in (None, "", [], {}):
                merged[key] = deepcopy(value)
    field_state = (
        emvr_design.get("field_state", {})
        if isinstance(emvr_design, dict)
        else {}
    )
    if isinstance(field_state, dict):
        for key, value in field_state.items():
            if key in EMVR_EDITABLE_FIELDS and value not in (None, "", [], {}):
                merged[key] = deepcopy(value)
    return merged


def normalize_emvr_design_update(raw: Any) -> dict[str, Any]:
    """Validate the semantic EMVR design roles without interpreting wording."""

    if not isinstance(raw, dict):
        return {}

    def text_list(key: str, *, limit: int = 12) -> list[str]:
        value = raw.get(key, [])
        if not isinstance(value, list):
            return []
        return list(
            dict.fromkeys(
                str(item).strip()[:600]
                for item in value
                if isinstance(item, str) and item.strip()
            )
        )[:limit]

    theory_links: list[dict[str, str]] = []
    seen_relations: set[str] = set()
    raw_links = raw.get("theory_links", [])
    for item in raw_links if isinstance(raw_links, list) else []:
        if not isinstance(item, dict):
            continue
        relation_id = str(item.get("relation_id") or "").strip().upper()
        supports = str(item.get("supports_design_content") or "").strip()[:1200]
        if (
            relation_id not in EMVR_THEORY_RELATION_IDS
            or not supports
            or relation_id in seen_relations
        ):
            continue
        theory_links.append(
            {
                "relation_id": relation_id,
                "supports_design_content": supports,
            }
        )
        seen_relations.add(relation_id)
    summary = str(raw.get("research_summary") or "").strip()[:1200]
    scalar_values = {
        key: str(raw.get(key) or "").strip()[:1600] or None
        for key in EMVR_SCALAR_FIELDS
    }
    field_updates: list[dict[str, Any]] = []
    raw_field_updates = raw.get("field_updates", [])
    for item in raw_field_updates if isinstance(raw_field_updates, list) else []:
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("field_id") or "").strip()
        operation = str(item.get("operation") or "").strip().upper()
        if field_id not in EMVR_EDITABLE_FIELDS or operation not in {
            "REPLACE", "MERGE", "CLEAR"
        }:
            continue
        value = _nonempty_field_value(field_id, item.get("value"))
        if operation == "CLEAR" or value is not None:
            field_updates.append(
                {"field_id": field_id, "operation": operation, "value": deepcopy(value)}
            )
    normalized = {
        **scalar_values,
        "research_summary": summary or scalar_values.get("research_summary"),
        "learning_objectives": text_list("learning_objectives"),
        "changed_quantities": text_list("changed_quantities"),
        "observed_quantities": text_list("observed_quantities"),
        "comparison_cases": text_list("comparison_cases"),
        "required_behaviors": text_list("required_behaviors"),
        "object_constraints": text_list("object_constraints"),
        "procedure_steps": text_list("procedure_steps", limit=20),
        "visualization_requirements": text_list("visualization_requirements"),
        "limitations": text_list("limitations"),
        "field_updates": field_updates,
        # Relation IDs are derived from explicit semantic links.  A bare ID
        # list is intentionally ignored so a model cannot attach a theory just
        # because it is topically nearby.
        "theory_links": theory_links,
        "theory_relation_ids": [item["relation_id"] for item in theory_links],
    }
    return normalized if any(value for value in normalized.values()) else {}


def formulas_for_emvr_relations(
    relation_ids: Iterable[str],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return formulas selected by persisted physical roles, never raw text."""

    catalog = {
        str(formula.get("id") or ""): formula
        for formula in KNOWLEDGE.formulas
        if isinstance(formula, dict)
    }
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_relation_id in relation_ids:
        relation_id = str(raw_relation_id).strip().upper()
        relation = EMVR_THEORY_RELATIONS.get(relation_id)
        if not relation:
            continue
        for formula_id in relation["formula_ids"]:
            if formula_id in seen or formula_id not in catalog:
                continue
            item = deepcopy(catalog[formula_id])
            item["supports_relation_id"] = relation_id
            item["supports_relation"] = relation["label"]
            selected.append(item)
            seen.add(formula_id)
            if len(selected) >= limit:
                return selected
    return selected


def emvr_formula_support_map(
    relation_ids: Iterable[str],
    requirements: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    requirements = requirements if isinstance(requirements, dict) else {}
    changed = requirements.get("changed_quantities", [])
    observed = requirements.get("observed_quantities", [])
    research_summary = str(requirements.get("research_summary") or "").strip()
    theory_links = requirements.get("theory_links", [])
    support_by_relation = {
        str(item.get("relation_id") or ""): str(
            item.get("supports_design_content") or ""
        ).strip()
        for item in theory_links
        if isinstance(item, dict)
        and str(item.get("relation_id") or "").strip()
        and str(item.get("supports_design_content") or "").strip()
    } if isinstance(theory_links, list) else {}
    supports = research_summary or "；".join(
        [
            *(str(item) for item in changed if str(item).strip()),
            *(str(item) for item in observed if str(item).strip()),
        ]
    )
    return [
        {
            "formula_id": formula["id"],
            "relation_id": formula["supports_relation_id"],
            "relation": formula["supports_relation"],
            "supports_design_content": support_by_relation.get(
                formula["supports_relation_id"],
                supports or formula["supports_relation"],
            ),
        }
        for formula in formulas_for_emvr_relations(relation_ids)
    ]
