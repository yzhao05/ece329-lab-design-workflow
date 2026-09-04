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

# A theory relation is persisted only when it is bound to committed design
# structure.  These are semantic field identifiers produced by the resolver;
# this module never tries to infer them from words in the student's message.
EMVR_THEORY_SUPPORT_FIELDS = frozenset(
    {
        "research_question",
        "changed_quantities",
        "observed_quantities",
        "comparison_cases",
        "object_constraints",
    }
)

EMVR_OBJECTIVE_FIELDS = (
    "conceptual_objective",
    "calculation_objective",
    "analysis_objective",
    "vr_interaction_objective",
    "observation_objective",
)


def candidate_formulas_for_emvr_context(
    text: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return grounded formula candidates with canonical relation bindings.

    This is a candidate pool for the model's contextual selection, not a
    decision that every formula belongs in the experiment. Formulas outside
    the explicit EMVR relation catalog are excluded, and each candidate names
    the one physical role that the output validator will require.
    """

    relation_by_formula = {
        str(formula_id): relation_id
        for relation_id, relation in EMVR_THEORY_RELATIONS.items()
        for formula_id in relation.get("formula_ids", [])
    }
    # Formula order in the JSON catalog is pedagogical, not a relevance
    # ranking.  First select the best-matching course concept, then expose only
    # formulas tied to that concept.  This prevents broad overview formulas
    # from an adjacent lecture (for example a general force law) from being
    # offered merely because the experiment also mentions an electric field.
    ranked_concept_ids = [
        str(item.get("id") or "")
        for item in KNOWLEDGE.match_concepts(text, limit=5)
        if str(item.get("id") or "")
    ]
    for concept_id in ranked_concept_ids:
        candidates: list[dict[str, Any]] = []
        for formula in KNOWLEDGE.formulas:
            formula_id = str(formula.get("id") or "")
            relation_id = relation_by_formula.get(formula_id)
            if (
                not relation_id
                or concept_id not in formula.get("concept_ids", [])
            ):
                continue
            candidates.append(
                {
                    **deepcopy(formula),
                    "supports_relation_id": relation_id,
                }
            )
            if len(candidates) >= limit:
                break
        if candidates:
            return candidates

    # Supplemental sources map back to lecture concepts through the knowledge
    # base.  Keep that established retrieval behavior as a final candidate
    # source, while retaining the canonical EMVR relation binding.
    candidates = []
    for formula in KNOWLEDGE.formula_references(text, limit=max(limit * 2, 16)):
        formula_id = str(formula.get("id") or "")
        relation_id = relation_by_formula.get(formula_id)
        if relation_id:
            candidates.append(
                {
                    **deepcopy(formula),
                    "supports_relation_id": relation_id,
                }
            )
        if len(candidates) >= limit:
            break
    return candidates

# Semantic design fields used by the intent resolver and state machine.  They
# are deliberately independent of any wording in the student's message.
EMVR_SCALAR_FIELDS = frozenset(
    {
        # ``experiment_brief`` is the authoritative, complete statement of the
        # student's EMVR direction.  The remaining fields are projections used
        # for validation, Builder handoff and stage-specific discussion; none
        # of them may replace the full brief on its own.
        "experiment_brief",
        "research_object",
        "direction_summary",
        "research_summary",
        "course_relationship",
        "research_question",
        "hypothesis",
        "design_rationale",
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
        "observation_objective",
        "lab_title",
        "lab_id",
        "desktop_interaction_plan",
        "room_spatial_requirements",
        "hidden_object_lifecycle",
    }
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
        "design_values",
        "limitations",
        "parameter_specifications",
        "expected_results",
        "acceptance_criteria",
        "report_questions",
    }
)
EMVR_EDITABLE_FIELDS = EMVR_SCALAR_FIELDS | EMVR_LIST_FIELDS


def emvr_stage_one_readiness(emvr_design: Any) -> dict[str, Any]:
    """Return structural Stage 1 readiness without interpreting raw wording.

    A non-empty sentence is not sufficient.  EMVR can leave the idea stage only
    after the semantic layer has separated the complete brief into an object,
    an operation/change and an observable response.  This keeps a mode command
    or a research-object fragment from masquerading as a complete direction.
    """

    requirements = merge_emvr_structured_requirements(emvr_design)
    brief = str(requirements.get("experiment_brief") or "").strip()
    research_object = str(requirements.get("research_object") or "").strip()
    object_constraints = requirements.get("object_constraints", [])
    changed = requirements.get("changed_quantities", [])
    behaviors = requirements.get("required_behaviors", [])
    observed = requirements.get("observed_quantities", [])

    def has_items(value: Any) -> bool:
        return isinstance(value, list) and any(str(item).strip() for item in value)

    checks = {
        "experiment_brief": bool(brief),
        "research_object": bool(research_object) or has_items(object_constraints),
        "operation_or_change": has_items(changed) or has_items(behaviors),
        "observation": has_items(observed),
    }
    labels = {
        "experiment_brief": "完整实验方向",
        "research_object": "实验对象",
        "operation_or_change": "核心操作或变化条件",
        "observation": "需要观察的现象",
    }
    missing = [labels[key] for key, ready in checks.items() if not ready]
    return {
        "ready": not missing,
        "checks": checks,
        "missing": missing,
        "missing_fields": [key for key, ready in checks.items() if not ready],
    }


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


def _objective_values(field_state: dict[str, Any]) -> list[str]:
    """Return the five independent objectives without collapsing siblings."""

    return list(
        dict.fromkeys(
            str(field_state.get(field_id) or "").strip()
            for field_id in EMVR_OBJECTIVE_FIELDS
            if str(field_state.get(field_id) or "").strip()
        )
    )


def _sync_learning_objective_summary(
    field_state: dict[str, Any],
    previous_component_values: list[str],
) -> None:
    """Refresh the aggregate view while preserving every untouched objective.

    ``learning_objectives`` is a report-oriented aggregate.  The five named
    objective fields are authoritative.  Replacing one of them therefore
    removes only its previous value from the aggregate and leaves all other
    named or legacy objectives intact.
    """

    component_values = _objective_values(field_state)
    if not component_values:
        return
    aggregate = field_state.get("learning_objectives", [])
    aggregate = aggregate if isinstance(aggregate, list) else []
    legacy_values = [
        str(item).strip()
        for item in aggregate
        if str(item).strip()
        and str(item).strip() not in set(previous_component_values)
    ]
    field_state["learning_objectives"] = list(
        dict.fromkeys([*legacy_values, *component_values])
    )


def _bound_theory_link(
    link: Any,
    requirements: dict[str, Any],
) -> dict[str, Any] | None:
    """Bind a theory claim to the current committed design or reject it.

    Membership in ECE329 is not enough.  The semantic resolver must name the
    design fields supported by the relation, and those fields must already
    contain committed values.  Persisting snapshots makes the decision
    auditable without searching student text for theory-specific keywords.
    """

    if not isinstance(link, dict):
        return None
    relation_id = str(link.get("relation_id") or "").strip().upper()
    support_text = str(link.get("supports_design_content") or "").strip()[:1200]
    raw_fields = link.get("supports_design_fields", [])
    support_fields = list(
        dict.fromkeys(
            str(field)
            for field in raw_fields
            if isinstance(field, str) and field in EMVR_THEORY_SUPPORT_FIELDS
        )
    ) if isinstance(raw_fields, list) else []
    if relation_id not in EMVR_THEORY_RELATION_IDS or not support_text or not support_fields:
        return None

    prior_bindings = link.get("design_bindings", [])
    prior_binding_map = {
        str(item.get("field_id") or ""): deepcopy(item.get("committed_value"))
        for item in prior_bindings
        if isinstance(item, dict) and str(item.get("field_id") or "")
    } if isinstance(prior_bindings, list) else {}
    bindings: list[dict[str, Any]] = []
    for field_id in support_fields:
        value = requirements.get(field_id)
        if value in (None, "", [], {}):
            continue
        # Persisted bindings are valid only for the exact committed design
        # snapshot that was semantically approved. If a supported field later
        # changes, the old theory must be selected again rather than silently
        # attaching itself to a different research question or observation.
        if field_id in prior_binding_map and prior_binding_map[field_id] != value:
            return None
        bindings.append(
            {
                "field_id": field_id,
                "committed_value": deepcopy(value),
            }
        )
    if not bindings:
        return None
    return {
        "relation_id": relation_id,
        "supports_design_content": support_text,
        "supports_design_fields": [item["field_id"] for item in bindings],
        "design_bindings": bindings,
    }


def apply_emvr_field_updates(
    emvr_design: dict[str, Any],
    structured_update: dict[str, Any],
) -> None:
    """Apply each semantic field edit independently and preserve siblings."""

    field_state = emvr_design.setdefault("field_state", {})
    if not isinstance(field_state, dict):
        field_state = {}
        emvr_design["field_state"] = field_state

    previous_objective_values = _objective_values(field_state)
    raw_edits = structured_update.get("field_updates", [])
    edits = raw_edits if isinstance(raw_edits, list) else []
    # Reject a common malformed projection in which the model copies one
    # broad experiment sentence into every physical role.  An authoritative
    # brief may contain the complete thought, but it does not by itself prove
    # that the research object, operation, variable and observation have each
    # been separated.  This compares structured values, not student wording.
    role_fields = {
        "research_object",
        "course_relationship",
        "research_question",
        "learning_objectives",
        "conceptual_objective",
        "calculation_objective",
        "analysis_objective",
        "vr_interaction_objective",
        "observation_objective",
        "changed_quantities",
        "observed_quantities",
        "required_behaviors",
        "object_constraints",
    }

    def value_signature(value: Any) -> str:
        if isinstance(value, list) and len(value) == 1:
            return value_signature(value[0])
        if isinstance(value, str):
            return " ".join(value.split()).casefold()
        return repr(value)

    brief_signatures = {
        value_signature(item.get("value"))
        for item in edits
        if isinstance(item, dict)
        and str(item.get("field_id") or "") == "experiment_brief"
        and str(item.get("operation") or "").upper() != "CLEAR"
        and item.get("value") not in (None, "", [], {})
    }
    existing_brief = _nonempty_field_value(
        "experiment_brief",
        field_state.get("experiment_brief"),
    )
    if existing_brief is not None:
        brief_signatures.add(value_signature(existing_brief))
    cloned_role_fields = {
        str(item.get("field_id") or "")
        for item in edits
        if isinstance(item, dict)
        and str(item.get("field_id") or "") in role_fields
        and value_signature(item.get("value")) in brief_signatures
    }
    if cloned_role_fields:
        edits = [
            item
            for item in edits
            if not (
                isinstance(item, dict)
                and str(item.get("field_id") or "") in cloned_role_fields
                and value_signature(item.get("value")) in brief_signatures
            )
        ]
    # Snapshot fields keep older clients compatible on ordinary answer turns.
    # Once explicit edits exist, however, only those targeted operations may
    # mutate field_state.  A model-provided stale snapshot must not overwrite
    # an unrelated field that the student did not ask to change.
    touched_fields: set[str] = set()
    if not edits:
        snapshot_brief = _nonempty_field_value(
            "experiment_brief",
            structured_update.get("experiment_brief"),
        )
        snapshot_brief_signature = (
            value_signature(snapshot_brief) if snapshot_brief is not None else ""
        )
        if not snapshot_brief_signature and existing_brief is not None:
            snapshot_brief_signature = value_signature(existing_brief)
        for field_id in EMVR_EDITABLE_FIELDS:
            if field_id not in structured_update:
                continue
            value = _nonempty_field_value(field_id, structured_update.get(field_id))
            if (
                field_id in role_fields
                and snapshot_brief_signature
                and value is not None
                and value_signature(value) == snapshot_brief_signature
            ):
                continue
            if value is not None:
                field_state[field_id] = deepcopy(value)
                touched_fields.add(field_id)

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
            touched_fields.add(field_id)
        elif operation == "REPLACE" and value is not None:
            field_state[field_id] = deepcopy(value)
            touched_fields.add(field_id)
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
            touched_fields.add(field_id)

    if touched_fields & {*EMVR_OBJECTIVE_FIELDS, "learning_objectives"}:
        _sync_learning_objective_summary(field_state, previous_objective_values)

    # Keep the authoritative direction aligned with later corrections.  A
    # broad initial title such as "静电场实验" is useful as a starting point,
    # but it must not remain the final direction after the student has supplied
    # a precise object, operation/change and observation.  Synthesis uses only
    # committed canonical fields and never invents a missing design detail.
    # An explicit experiment_brief edit remains authoritative for that turn.
    core_direction_fields = {
        "research_object",
        "required_behaviors",
        "changed_quantities",
        "observed_quantities",
        "comparison_cases",
        "learning_objectives",
    }
    if (
        touched_fields & core_direction_fields
        and "experiment_brief" not in touched_fields
    ):
        research_object = str(field_state.get("research_object") or "").strip()

        def items(field_id: str) -> list[str]:
            raw = field_state.get(field_id, [])
            return (
                [str(item).strip() for item in raw if str(item).strip()]
                if isinstance(raw, list)
                else []
            )

        behaviors = items("required_behaviors")
        changed = items("changed_quantities")
        observed = items("observed_quantities")
        comparisons = items("comparison_cases")
        objectives = items("learning_objectives")
        if research_object and (behaviors or changed) and observed:
            parts = [f"研究对象：{research_object}"]
            if behaviors:
                parts.append(f"核心操作：{'、'.join(behaviors)}")
            if changed:
                parts.append(f"变化条件：{'、'.join(changed)}")
            parts.append(f"观察内容：{'、'.join(observed)}")
            if comparisons:
                parts.append(f"比较情形：{'、'.join(comparisons)}")
            if objectives:
                parts.append(f"学习目标：{'、'.join(objectives)}")
            synthesized = "；".join(parts)
            field_state["experiment_brief"] = synthesized
            emvr_design["experiment_brief"] = synthesized
            emvr_design["current_brief"] = synthesized
            emvr_design["brief"] = synthesized
            emvr_design["brief_source"] = "STRUCTURED_FIELD_SYNTHESIS"

    # Keep the formula-driven structured brief synchronized with later
    # field-level corrections.  The Stage 1 object is not a frozen transcript:
    # it is the Builder-facing design contract, so exports must not retain an
    # old variable or observation after the student revised it elsewhere.
    authoritative = emvr_design.get("authoritative_experiment_brief")
    if isinstance(authoritative, dict):
        if "experiment_brief" in touched_fields:
            summary = str(field_state.get("experiment_brief") or "").strip()
            if summary:
                authoritative["summary"] = summary
            else:
                authoritative.pop("summary", None)
        if touched_fields & {"direction_summary", "research_summary"}:
            topic = str(
                field_state.get("direction_summary")
                or field_state.get("research_summary")
                or ""
            ).strip()
            if topic:
                authoritative["topic"] = topic
        if "research_object" in touched_fields:
            research_object = str(field_state.get("research_object") or "").strip()
            authoritative["objects"] = [research_object] if research_object else []
        for field_id, brief_field in (
            ("required_behaviors", "operations"),
            ("changed_quantities", "changed_quantities"),
            ("observed_quantities", "observed_quantities"),
            ("object_constraints", "boundary_conditions"),
        ):
            if field_id not in touched_fields:
                continue
            value = field_state.get(field_id, [])
            authoritative[brief_field] = (
                list(value) if isinstance(value, list) else []
            )
        formula_flow = emvr_design.get("formula_flow")
        if isinstance(formula_flow, dict):
            formula_flow["experiment_brief"] = deepcopy(authoritative)

    raw_theory_links = structured_update.get("theory_links", [])
    raw_theory_links = raw_theory_links if isinstance(raw_theory_links, list) else []
    theory_edits = structured_update.get("theory_link_updates", [])
    theory_edits = theory_edits if isinstance(theory_edits, list) else []
    has_theory_change = bool(raw_theory_links or theory_edits)
    theory_state = emvr_design.get("theory_link_state")
    if has_theory_change and not isinstance(theory_state, dict):
        # Migrate an older session lazily.  Seed the editable state with the
        # last persisted per-stage theory links before applying an ADD/REMOVE,
        # so an unrelated first edit cannot erase them and a targeted removal
        # removes only the requested relation.
        theory_state = {}
        by_stage = emvr_design.get("structured_requirements", {})
        if isinstance(by_stage, dict):
            for stage in Stage:
                prior = by_stage.get(stage.value)
                if not isinstance(prior, dict):
                    continue
                for link in prior.get("theory_links", []):
                    if not isinstance(link, dict):
                        continue
                    relation_id = str(link.get("relation_id") or "")
                    if relation_id in EMVR_THEORY_RELATION_IDS:
                        theory_state[relation_id] = deepcopy(link)
        emvr_design["theory_link_state"] = theory_state
    if not isinstance(theory_state, dict):
        return
    current_requirements = merge_emvr_structured_requirements(emvr_design)
    # Revalidate existing relations after every field-level edit. This makes a
    # theory decision dependent on the design version it actually supported,
    # without inspecting the student's wording or maintaining formula-specific
    # exclusion lists.
    for relation_id, existing_link in list(theory_state.items()):
        rebound = _bound_theory_link(existing_link, current_requirements)
        if rebound is None:
            theory_state.pop(relation_id, None)
        else:
            theory_state[relation_id] = rebound
    if not theory_edits:
        for link in raw_theory_links:
            bound = _bound_theory_link(link, current_requirements)
            if bound is not None:
                theory_state[bound["relation_id"]] = bound
    for edit in theory_edits:
        if not isinstance(edit, dict):
            continue
        relation_id = str(edit.get("relation_id") or "")
        operation = str(edit.get("operation") or "").upper()
        if relation_id not in EMVR_THEORY_RELATION_IDS:
            continue
        if operation == "REMOVE":
            theory_state.pop(relation_id, None)
        elif operation == "ADD":
            link = edit.get("link")
            bound = _bound_theory_link(link, current_requirements)
            if bound is not None and bound["relation_id"] == relation_id:
                theory_state[relation_id] = bound


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
    theory_state = (
        emvr_design.get("theory_link_state", {})
        if isinstance(emvr_design, dict)
        else {}
    )
    if (
        isinstance(emvr_design, dict)
        and "theory_link_state" in emvr_design
        and isinstance(theory_state, dict)
    ):
        links = [
            deepcopy(link)
            for relation_id, link in theory_state.items()
            if relation_id in EMVR_THEORY_RELATION_IDS
            and isinstance(link, dict)
        ]
        merged["theory_links"] = links
        merged["theory_relation_ids"] = [
            str(link.get("relation_id") or "")
            for link in links
            if str(link.get("relation_id") or "")
        ]
    # Revalidate both current and migrated sessions against the committed
    # design.  A relation that merely appears in a stage snapshot, or whose
    # declared support fields are empty, is not part of the final design.
    bound_links: list[dict[str, Any]] = []
    seen_relations: set[str] = set()
    raw_links = merged.get("theory_links", [])
    for link in raw_links if isinstance(raw_links, list) else []:
        bound = _bound_theory_link(link, merged)
        if bound is None or bound["relation_id"] in seen_relations:
            continue
        bound_links.append(bound)
        seen_relations.add(bound["relation_id"])
    if bound_links:
        merged["theory_links"] = bound_links
        merged["theory_relation_ids"] = [
            link["relation_id"] for link in bound_links
        ]
    else:
        merged.pop("theory_links", None)
        merged.pop("theory_relation_ids", None)
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

    theory_links: list[dict[str, Any]] = []
    seen_relations: set[str] = set()
    raw_links = raw.get("theory_links", [])
    for item in raw_links if isinstance(raw_links, list) else []:
        if not isinstance(item, dict):
            continue
        relation_id = str(item.get("relation_id") or "").strip().upper()
        supports = str(item.get("supports_design_content") or "").strip()[:1200]
        support_fields = list(
            dict.fromkeys(
                str(field)
                for field in item.get("supports_design_fields", [])
                if isinstance(field, str)
                and field in EMVR_THEORY_SUPPORT_FIELDS
            )
        ) if isinstance(item.get("supports_design_fields"), list) else []
        if (
            relation_id not in EMVR_THEORY_RELATION_IDS
            or not supports
            or not support_fields
            or relation_id in seen_relations
        ):
            continue
        theory_links.append(
            {
                "relation_id": relation_id,
                "supports_design_content": supports,
                **(
                    {"supports_design_fields": support_fields}
                    if support_fields
                    else {}
                ),
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
    theory_link_updates: list[dict[str, Any]] = []
    raw_theory_updates = raw.get("theory_link_updates", [])
    for item in raw_theory_updates if isinstance(raw_theory_updates, list) else []:
        if not isinstance(item, dict):
            continue
        relation_id = str(item.get("relation_id") or "").strip().upper()
        operation = str(item.get("operation") or "").strip().upper()
        if relation_id not in EMVR_THEORY_RELATION_IDS or operation not in {
            "ADD",
            "REMOVE",
        }:
            continue
        if operation == "REMOVE":
            theory_link_updates.append(
                {"relation_id": relation_id, "operation": "REMOVE"}
            )
            continue
        link = next(
            (
                deepcopy(link)
                for link in theory_links
                if link.get("relation_id") == relation_id
            ),
            None,
        )
        if isinstance(link, dict):
            theory_link_updates.append(
                {
                    "relation_id": relation_id,
                    "operation": "ADD",
                    "link": link,
                }
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
        "design_values": text_list("design_values"),
        "limitations": text_list("limitations"),
        "parameter_specifications": text_list("parameter_specifications", limit=20),
        "expected_results": text_list("expected_results", limit=20),
        "acceptance_criteria": text_list("acceptance_criteria", limit=20),
        "report_questions": text_list("report_questions", limit=20),
        "field_updates": field_updates,
        "theory_link_updates": theory_link_updates,
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
    return [
        {
            "formula_id": formula["id"],
            "relation_id": formula["supports_relation_id"],
            "relation": formula["supports_relation"],
            "supports_design_content": support_by_relation[
                formula["supports_relation_id"]
            ],
        }
        for formula in formulas_for_emvr_relations(relation_ids)
        if formula["supports_relation_id"] in support_by_relation
    ]
