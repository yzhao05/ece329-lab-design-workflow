from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .emvr_design import merge_emvr_structured_requirements
from .knowledge_base import KNOWLEDGE
from .models import DesignSession, InteractionState, Stage, StepOutput


TOPIC_RECEIVED = "TOPIC_RECEIVED"
FORMULA_CANDIDATES_PRESENTED = "FORMULA_CANDIDATES_PRESENTED"
FORMULA_SELECTION_CONFIRMED = "FORMULA_SELECTION_CONFIRMED"
FORMULA_COMPOSITION_REVIEW = "FORMULA_COMPOSITION_REVIEW"
EXPERIMENT_METHODS_PRESENTED = "EXPERIMENT_METHODS_PRESENTED"
# Compatibility alias for stored sessions created before the pattern generator.
FORMULA_SCENES_PRESENTED = EXPERIMENT_METHODS_PRESENTED
EXPERIMENT_DIRECTION_REVIEW = "EXPERIMENT_DIRECTION_REVIEW"
EXPERIMENT_DIRECTION_LOCKED = "EXPERIMENT_DIRECTION_LOCKED"
EXPERIMENT_BRIEF_COMPLETED = "EXPERIMENT_BRIEF_COMPLETED"
EMVR_DETAIL_DESIGN = "EMVR_DETAIL_DESIGN"

EMVR_FORMULA_ACTION_TYPES = frozenset(
    {
        "SET_EMVR_TOPIC",
        "SELECT_EMVR_FORMULAS",
        "SET_EMVR_FORMULA_COMPOSITION",
        "SELECT_EMVR_EXPERIMENT_METHODS",
        "REVISE_EMVR_DIRECTION",
        "LOCK_EMVR_DIRECTION",
    }
)

_SPECIFICITY = frozenset({"BROAD", "PARTIALLY_DEFINED", "SPECIFIC"})
_COURSE_DOMAINS = frozenset(
    str(profile.get("course_block") or "")
    for profile in KNOWLEDGE.formula_design_profiles
    if str(profile.get("course_block") or "")
)
_FORMULA_IDS = frozenset(
    str(formula.get("id") or "")
    for formula in KNOWLEDGE.formulas
    if str(formula.get("id") or "")
)
_PROFILE_IDS = frozenset(
    str(profile.get("profile_id") or "")
    for profile in KNOWLEDGE.formula_design_profiles
    if str(profile.get("profile_id") or "")
)
_PATTERN_IDS = frozenset(
    str(pattern.get("pattern_id") or "")
    for pattern in KNOWLEDGE.experiment_design_patterns
    if str(pattern.get("pattern_id") or "")
)
_COMPOSITION_STRATEGIES = frozenset({"SINGLE", "COMBINED", "SEPARATE_THEN_COMBINE"})


def _unique_text(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            str(item).strip()[:400]
            for item in value
            if isinstance(item, str) and item.strip()
        )
    )[:limit]


def normalize_topic_analysis(raw: Any) -> dict[str, Any] | None:
    """Validate semantic topic analysis without deriving formulas from words."""

    if not isinstance(raw, dict):
        return None
    topic = str(raw.get("topic_description") or "").strip()[:1200]
    domain = str(raw.get("course_domain") or "").strip().casefold()
    if not topic or domain not in _COURSE_DOMAINS:
        return None
    specificity = str(raw.get("specificity") or "BROAD").strip().upper()
    if specificity not in _SPECIFICITY:
        specificity = "BROAD"
    explicit_formula_ids = [
        formula_id
        for formula_id in dict.fromkeys(
            str(item).strip()
            for item in raw.get("explicit_formula_ids", [])
            if isinstance(item, str)
        )
        if formula_id in _FORMULA_IDS
    ] if isinstance(raw.get("explicit_formula_ids"), list) else []
    evidence: list[dict[str, Any]] = []
    for item in raw.get("profile_evidence", []) \
        if isinstance(raw.get("profile_evidence"), list) else []:
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("profile_id") or "").strip()
        if profile_id not in _PROFILE_IDS:
            continue
        evidence.append(
            {
                "profile_id": profile_id,
                "course_concept_match": item.get("course_concept_match") is True,
                "variation_match": item.get("variation_match") is True,
                "observation_match": item.get("observation_match") is True,
                "object_geometry_match": item.get("object_geometry_match") is True,
                "boundary_match": item.get("boundary_match") is True,
                "condition_conflict": item.get("condition_conflict") is True,
            }
        )
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "course_domain": domain,
        "topic_description": topic,
        "mentioned_objects": _unique_text(raw.get("mentioned_objects")),
        "changed_quantities": _unique_text(raw.get("changed_quantities")),
        "observed_quantities": _unique_text(raw.get("observed_quantities")),
        "explicit_formula_ids": explicit_formula_ids,
        "specificity": specificity,
        "profile_evidence": evidence,
        "confidence": max(0.0, min(confidence, 1.0)),
    }


def normalize_formula_flow_action(act_type: str, raw: Any) -> dict[str, Any] | None:
    if act_type == "SET_EMVR_TOPIC":
        return normalize_topic_analysis(raw)
    if not isinstance(raw, dict):
        return None
    if act_type == "SELECT_EMVR_FORMULAS":
        primary = [
            item for item in _unique_text(raw.get("primary_profile_ids"), limit=6)
            if item in _PROFILE_IDS
        ]
        supporting = [
            item for item in _unique_text(raw.get("supporting_profile_ids"), limit=6)
            if item in _PROFILE_IDS and item not in primary
        ]
        if not primary:
            return None
        return {
            "primary_profile_ids": primary,
            "supporting_profile_ids": supporting,
            "primary_formula_ids": [
                item
                for item in _unique_text(raw.get("primary_formula_ids"), limit=12)
                if item in _FORMULA_IDS
            ],
            "supporting_formula_ids": [
                item
                for item in _unique_text(raw.get("supporting_formula_ids"), limit=12)
                if item in _FORMULA_IDS
            ],
            "student_rationale": str(raw.get("student_rationale") or "").strip()[:1000] or None,
        }
    if act_type == "SET_EMVR_FORMULA_COMPOSITION":
        strategy = str(raw.get("strategy") or "").strip().upper()
        if strategy not in _COMPOSITION_STRATEGIES:
            return None
        return {
            "strategy": strategy,
            "student_rationale": str(raw.get("student_rationale") or "").strip()[:1000] or None,
        }
    if act_type == "SELECT_EMVR_EXPERIMENT_METHODS":
        selected = _unique_text(raw.get("selected_method_ids"), limit=15)
        if not selected:
            return None
        return {
            "selected_method_ids": selected,
            "custom_direction": str(raw.get("custom_direction") or "").strip()[:1600] or None,
            "objects": _unique_text(raw.get("objects")),
            "operations": _unique_text(raw.get("operations")),
            "changed_quantities": _unique_text(raw.get("changed_quantities")),
            "observed_quantities": _unique_text(raw.get("observed_quantities")),
            "comparison_cases": _unique_text(raw.get("comparison_cases")),
            "boundary_conditions": _unique_text(raw.get("boundary_conditions")),
        }
    if act_type in {"REVISE_EMVR_DIRECTION", "LOCK_EMVR_DIRECTION"}:
        normalized_updates: dict[str, dict[str, Any]] = {}
        source_updates = raw.get("brief_updates", {})
        source_updates = source_updates if isinstance(source_updates, dict) else {}
        for field_id in (
            "topic",
            "objects",
            "operations",
            "changed_quantities",
            "observed_quantities",
            "comparison_cases",
            "boundary_conditions",
        ):
            raw_update = source_updates.get(field_id)
            if isinstance(raw_update, dict):
                operation = str(raw_update.get("operation") or "REPLACE").upper()
                value = raw_update.get("value")
            else:
                operation = "REPLACE"
                value = raw_update
            if operation not in {"MERGE", "REPLACE", "CLEAR"}:
                continue
            if operation == "CLEAR":
                normalized_updates[field_id] = {"operation": operation, "value": None}
            elif field_id == "topic" and isinstance(value, str) and value.strip():
                normalized_updates[field_id] = {
                    "operation": operation,
                    "value": value.strip()[:1600],
                }
            elif field_id != "topic" and isinstance(value, list):
                normalized_value = _unique_text(value)
                if normalized_value:
                    normalized_updates[field_id] = {
                        "operation": operation,
                        "value": normalized_value,
                    }
        return {
            "student_rationale": str(raw.get("student_rationale") or "").strip()[:1000] or None,
            "brief_updates": normalized_updates,
        }
    return None


def ensure_emvr_formula_flow(session: DesignSession) -> dict[str, Any]:
    emvr = session.design_context.setdefault("emvr_design", {})
    if not isinstance(emvr, dict):
        emvr = {}
        session.design_context["emvr_design"] = emvr
    flow = emvr.setdefault("formula_flow", {})
    if not isinstance(flow, dict):
        flow = {}
        emvr["formula_flow"] = flow
    if flow.get("phase") == "FORMULA_SCENES_PRESENTED":
        # Existing Render sessions may still point at the retired fixed-scene
        # phase. Preserve their formula selection, then regenerate methods from
        # the new pattern layer instead of leaving the session without a route.
        flow["phase"] = FORMULA_SELECTION_CONFIRMED
        flow.pop("formula_scenes", None)
        flow.pop("scene_selection", None)
    if flow.get("phase") in {EXPERIMENT_DIRECTION_LOCKED, EXPERIMENT_BRIEF_COMPLETED}:
        # These were transient names in an earlier draft of the state model.
        # Recover according to persisted data instead of treating an unknown
        # branch as completed.
        if isinstance(emvr.get("authoritative_experiment_brief"), dict):
            flow["phase"] = EMVR_DETAIL_DESIGN
            flow["direction_locked"] = True
        elif isinstance(flow.get("experiment_brief"), dict):
            flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        else:
            flow["phase"] = TOPIC_RECEIVED
    known_phases = {
        TOPIC_RECEIVED,
        FORMULA_CANDIDATES_PRESENTED,
        FORMULA_SELECTION_CONFIRMED,
        FORMULA_COMPOSITION_REVIEW,
        EXPERIMENT_METHODS_PRESENTED,
        EXPERIMENT_DIRECTION_REVIEW,
        EMVR_DETAIL_DESIGN,
    }
    if flow.get("phase") not in known_phases:
        flow["phase"] = TOPIC_RECEIVED
    flow.setdefault("phase", TOPIC_RECEIVED)
    flow.setdefault(
        "formula_selection",
        {
            "candidate_profile_ids": [],
            "primary_formula_ids": [],
            "supporting_formula_ids": [],
            "primary_profile_ids": [],
            "supporting_profile_ids": [],
            "selection_status": "PENDING",
            "student_rationale": None,
        },
    )
    flow.setdefault("formula_composition", {"strategy": None, "status": "PENDING"})
    flow.setdefault(
        "method_selection",
        {
            "candidate_method_ids": [],
            "selected_method_ids": [],
            "selection_status": "PENDING",
        },
    )
    phase = str(flow.get("phase") or TOPIC_RECEIVED)
    selection = flow["formula_selection"]
    if phase == EXPERIMENT_DIRECTION_REVIEW and isinstance(
        flow.get("experiment_brief"), dict
    ):
        review_brief = flow["experiment_brief"]
        if not selection.get("primary_formula_ids") and review_brief.get(
            "primary_formula_ids"
        ):
            selection["primary_formula_ids"] = list(
                review_brief.get("primary_formula_ids", [])
            )
            selection["supporting_formula_ids"] = list(
                review_brief.get("supporting_formula_ids", [])
            )
            selection["selection_status"] = "CONFIRMED"
    if (
        phase == FORMULA_CANDIDATES_PRESENTED
        and (
            not isinstance(flow.get("formula_cards"), list)
            or not selection.get("candidate_profile_ids")
        )
    ):
        flow["phase"] = TOPIC_RECEIVED
    elif phase in {
        FORMULA_SELECTION_CONFIRMED,
        FORMULA_COMPOSITION_REVIEW,
        EXPERIMENT_METHODS_PRESENTED,
        EXPERIMENT_DIRECTION_REVIEW,
    } and not selection.get("primary_formula_ids"):
        flow["phase"] = TOPIC_RECEIVED
    elif (
        phase == EXPERIMENT_METHODS_PRESENTED
        and not (
            isinstance(flow.get("experiment_methods"), list)
            and flow.get("experiment_methods")
        )
    ):
        flow["phase"] = FORMULA_COMPOSITION_REVIEW
    elif (
        phase == EXPERIMENT_DIRECTION_REVIEW
        and not isinstance(flow.get("experiment_brief"), dict)
    ):
        flow["phase"] = (
            EXPERIMENT_METHODS_PRESENTED
            if isinstance(flow.get("experiment_methods"), list)
            and flow.get("experiment_methods")
            else FORMULA_COMPOSITION_REVIEW
        )
    return flow


def emvr_formula_flow_active(session: DesignSession) -> bool:
    if (
        session.interaction_state is not InteractionState.EMVR_DIRECT
        or session.current_stage is not Stage.IDEA_BRAINSTORMING
    ):
        return False
    flow = ensure_emvr_formula_flow(session)
    return flow.get("phase") != EMVR_DETAIL_DESIGN


def public_formula_flow_state(session: DesignSession) -> dict[str, Any] | None:
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return None
    flow = ensure_emvr_formula_flow(session)
    selection = flow.get("formula_selection", {})
    methods = flow.get("method_selection", {})
    return {
        "phase": flow.get("phase"),
        "topic_seed": str(flow.get("topic_seed") or "") or None,
        "topic_analysis": deepcopy(flow.get("topic_analysis")),
        "formula_selection": {
            "candidate_profile_ids": list(selection.get("candidate_profile_ids", [])),
            "primary_profile_ids": list(selection.get("primary_profile_ids", [])),
            "supporting_profile_ids": list(selection.get("supporting_profile_ids", [])),
            "primary_formula_ids": list(selection.get("primary_formula_ids", [])),
            "supporting_formula_ids": list(selection.get("supporting_formula_ids", [])),
            "selection_status": selection.get("selection_status", "PENDING"),
            "student_rationale": str(selection.get("student_rationale") or "")
            or None,
        },
        "formula_composition": deepcopy(flow.get("formula_composition")),
        "method_selection": {
            "candidate_method_ids": list(methods.get("candidate_method_ids", [])),
            "selected_method_ids": list(methods.get("selected_method_ids", [])),
            "selection_status": methods.get("selection_status", "PENDING"),
        },
        "coverage_matrix": deepcopy(flow.get("coverage_matrix")),
        "experiment_methods": [
            {
                "method_id": method.get("method_id"),
                "title": method.get("title"),
                "pattern_ids": list(method.get("pattern_ids", [])),
                "description": method.get("description"),
                "process_summary": method.get("process_summary"),
            }
            for method in flow.get("experiment_methods", [])
            if isinstance(method, dict)
        ],
        "experiment_brief": deepcopy(flow.get("experiment_brief")),
        # This is internal resolver context, not a student-facing status
        # message.  It lets the next successful semantic call recover work
        # that arrived while the intent service was unavailable.
        "semantic_recovery": deepcopy(flow.get("semantic_recovery")),
    }


def _profile_by_id(profile_id: str) -> dict[str, Any] | None:
    return next(
        (
            profile
            for profile in KNOWLEDGE.public_formula_design_profiles()
            if profile.get("profile_id") == profile_id
        ),
        None,
    )


def _formula_ids_for_profiles(profile_ids: Iterable[str]) -> tuple[list[str], list[str]]:
    primary: list[str] = []
    supporting: list[str] = []
    for profile_id in profile_ids:
        profile = _profile_by_id(profile_id)
        if not profile:
            continue
        for formula_id in profile.get("primary_formula_ids", []):
            if formula_id not in primary:
                primary.append(formula_id)
        for formula_id in profile.get("supporting_formula_ids", []):
            if formula_id not in primary and formula_id not in supporting:
                supporting.append(formula_id)
    return primary, supporting


def _formula_by_id(formula_id: str) -> dict[str, Any] | None:
    return next(
        (
            formula
            for formula in KNOWLEDGE.public_formulas()
            if formula.get("id") == formula_id
        ),
        None,
    )


def _formula_display(formula_id: str) -> str:
    """Return a student-facing formula label without exposing stable IDs."""

    formula = _formula_by_id(formula_id)
    if not formula:
        return "已确认的课程公式"
    name = str(formula.get("name") or "").strip()
    expression = str(formula.get("expression") or "").strip()
    if name and expression:
        return f"{name}（{expression}）"
    return name or expression or "已确认的课程公式"


def formula_support_map_for_selection(session: DesignSession) -> list[dict[str, Any]]:
    """Bind each confirmed formula to the experiment fields it supports."""

    emvr = session.design_context.get("emvr_design", {})
    if not isinstance(emvr, dict):
        return []
    flow = emvr.get("formula_flow", {})
    selection = flow.get("formula_selection", {}) if isinstance(flow, dict) else {}
    brief = emvr.get("authoritative_experiment_brief", {})
    if not isinstance(selection, dict) or not isinstance(brief, dict):
        return []
    # Later EMVR stages may refine the initially locked direction.  Bind
    # formulas to the latest canonical fields rather than to the old Stage 1
    # snapshot, otherwise the final report can silently describe a superseded
    # variable or observation.
    requirements = merge_emvr_structured_requirements(emvr)
    brief = deepcopy(brief)
    for target, source in (
        ("changed_quantities", "changed_quantities"),
        ("observed_quantities", "observed_quantities"),
        ("boundary_conditions", "object_constraints"),
    ):
        value = requirements.get(source)
        if isinstance(value, list) and any(str(item).strip() for item in value):
            brief[target] = [str(item).strip() for item in value if str(item).strip()]
    primary_formula_ids = list(selection.get("primary_formula_ids", []))
    supporting_formula_ids = list(selection.get("supporting_formula_ids", []))
    profile_ids = [
        *selection.get("primary_profile_ids", []),
        *selection.get("supporting_profile_ids", []),
    ]
    profiles = {
        str(profile.get("profile_id") or ""): profile
        for profile_id in profile_ids
        for profile in [_profile_by_id(str(profile_id))]
        if profile is not None
    }
    results: list[dict[str, Any]] = []
    for formula_id in dict.fromkeys([*primary_formula_ids, *supporting_formula_ids]):
        profile = next(
            (
                item
                for item in profiles.values()
                if formula_id
                in {
                    *item.get("primary_formula_ids", []),
                    *item.get("supporting_formula_ids", []),
                }
            ),
            None,
        )
        if not profile:
            continue
        changed = _unique_text(brief.get("changed_quantities"), limit=2)
        observed = _unique_text(brief.get("observed_quantities"), limit=2)
        boundaries = _unique_text(brief.get("boundary_conditions"), limit=1)
        supported_fields: list[str] = []
        details: list[str] = []
        if changed:
            supported_fields.append("changed_quantities")
            details.append(f"变化量：{'、'.join(changed)}")
        if observed:
            supported_fields.append("observed_quantities")
            details.append(f"观察量：{'、'.join(observed)}")
        if boundaries:
            supported_fields.append("object_constraints")
            details.append(f"适用边界：{'；'.join(boundaries)}")
        if not details:
            continue
        results.append(
            {
                "formula_id": formula_id,
                "relation_id": str(profile.get("profile_id") or ""),
                "relation": str(profile.get("title_zh") or "课程公式关系"),
                "supports_design_content": "；".join(details),
                "supports_design_fields": supported_fields,
            }
        )
    return results


def _reset_formula_choices(flow: dict[str, Any]) -> None:
    """Clear conclusions derived from an earlier topic analysis."""

    flow["formula_selection"] = {
        "candidate_profile_ids": [],
        "primary_formula_ids": [],
        "supporting_formula_ids": [],
        "primary_profile_ids": [],
        "supporting_profile_ids": [],
        "selection_status": "PENDING",
        "student_rationale": None,
    }
    flow["formula_composition"] = {"strategy": None, "status": "PENDING"}
    flow["method_selection"] = {
        "candidate_method_ids": [],
        "selected_method_ids": [],
        "selection_status": "PENDING",
    }
    for key in ("formula_cards", "experiment_methods", "coverage_matrix", "experiment_brief"):
        flow.pop(key, None)


def _apply_brief_updates(brief: dict[str, Any], updates: Any) -> None:
    if not isinstance(updates, dict):
        return
    for field_id, update in updates.items():
        if field_id not in {
            "topic",
            "objects",
            "operations",
            "changed_quantities",
            "observed_quantities",
            "comparison_cases",
            "boundary_conditions",
        } or not isinstance(update, dict):
            continue
        operation = str(update.get("operation") or "REPLACE").upper()
        value = update.get("value")
        if operation == "CLEAR":
            brief[field_id] = "" if field_id == "topic" else []
        elif field_id == "topic" and isinstance(value, str) and value.strip():
            if operation == "MERGE" and str(brief.get(field_id) or "").strip():
                existing = str(brief[field_id]).strip()
                brief[field_id] = existing if value.strip() in existing else f"{existing}；{value.strip()}"
            else:
                brief[field_id] = value.strip()[:1600]
        elif field_id != "topic" and isinstance(value, list):
            values = _unique_text(value)
            if operation == "MERGE":
                brief[field_id] = list(
                    dict.fromkeys([*_unique_text(brief.get(field_id)), *values])
                )
            else:
                brief[field_id] = values


def score_formula_profiles(topic_analysis: dict[str, Any], *, limit: int = 4) -> list[dict[str, Any]]:
    """Rank legal knowledge profiles from semantic evidence and stable IDs only."""

    domain = str(topic_analysis.get("course_domain") or "")
    explicit_formula_ids = set(topic_analysis.get("explicit_formula_ids", []))
    evidence = {
        str(item.get("profile_id") or ""): item
        for item in topic_analysis.get("profile_evidence", [])
        if isinstance(item, dict)
    }
    ranked: list[tuple[int, int, dict[str, Any], dict[str, int]]] = []
    for order, profile in enumerate(KNOWLEDGE.public_formula_design_profiles()):
        profile_id = str(profile.get("profile_id") or "")
        profile_formula_ids = {
            *profile.get("primary_formula_ids", []),
            *profile.get("supporting_formula_ids", []),
        }
        item = evidence.get(profile_id, {})
        breakdown = {
            "course_concept": 30
            if item.get("course_concept_match") is True
            or str(profile.get("course_block") or "") == domain
            else 0,
            "changed_quantity": 25 if item.get("variation_match") is True else 0,
            "observed_quantity": 25 if item.get("observation_match") is True else 0,
            "object_geometry": 10 if item.get("object_geometry_match") is True else 0,
            "boundary_condition": 10 if item.get("boundary_match") is True else 0,
            "condition_conflict": -40 if item.get("condition_conflict") is True else 0,
            "explicit_formula": 60 if explicit_formula_ids.intersection(profile_formula_ids) else 0,
        }
        score = sum(breakdown.values())
        if score <= 0:
            continue
        ranked.append((score, -order, profile, breakdown))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {**deepcopy(profile), "match_score": score, "score_breakdown": breakdown}
        for score, _, profile, breakdown in ranked[: max(1, limit)]
    ]


def _quantity_labels(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [
        str(item.get("quantity") or item.get("condition") or "").strip()
        for item in items
        if isinstance(item, dict)
        and str(item.get("quantity") or item.get("condition") or "").strip()
    ]


def _formula_cards(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for profile in profiles:
        cards.append(
            {
                "profile_id": profile["profile_id"],
                "option_id": f"emvr-formula:{profile['profile_id']}",
                "title": profile.get("title_zh"),
                "primary_formulas": [
                    {
                        "formula_id": formula.get("id"),
                        "name": formula.get("name"),
                        "expression": formula.get("expression"),
                    }
                    for formula in profile.get("primary_formulas", [])
                ],
                "supporting_formulas": [
                    {
                        "formula_id": formula.get("id"),
                        "name": formula.get("name"),
                        "expression": formula.get("expression"),
                    }
                    for formula in profile.get("supporting_formulas", [])
                ],
                "changed_quantities": _quantity_labels(profile.get("supported_variations")),
                "observed_quantities": _quantity_labels(profile.get("supported_observations")),
                "boundary_conditions": _quantity_labels(profile.get("boundary_conditions")),
                "match_score": profile.get("match_score"),
            }
        )
    return cards


def _format_formula_cards(cards: list[dict[str, Any]], topic: str) -> str:
    paragraphs = [
        f"你提出的是“{topic}”。我们先不急着填写物体、变量或流程，先确定这个实验真正要检验的理论关系。"
    ]
    for index, card in enumerate(cards, start=1):
        equations = "；".join(
            str(item.get("expression") or "")
            for item in card.get("primary_formulas", [])
            if str(item.get("expression") or "").strip()
        )
        optional_support = "；".join(
            f"{item.get('name')}（{item.get('expression')}）"
            for item in card.get("supporting_formulas", [])
            if str(item.get("name") or "").strip()
            and str(item.get("expression") or "").strip()
        )
        paragraphs.append(
            f"{index}. {card['title']}\n"
            f"公式：{equations}\n"
            f"可选辅助：{optional_support or '无'}\n"
            f"可改变：{'、'.join(card['changed_quantities'][:4])}\n"
            f"可观察：{'、'.join(card['observed_quantities'][:4])}\n"
            f"适用条件：{'；'.join(card['boundary_conditions'][:3])}"
        )
    paragraphs.append(
        "你更想围绕哪组公式展开？如果还不确定，我也可以先比较这些公式分别适合做成什么样的 Unity VR 实验；"
        "也可以组合一组主要公式和一组辅助公式。"
    )
    return "\n\n".join(paragraphs)


_PATTERN_SHORT_TITLES = {
    "FORWARD_VISUALIZATION": "展示",
    "SINGLE_PARAMETER_SWEEP": "扫描",
    "CONTROLLED_COMPARISON": "对照",
    "COMPONENT_DECOMPOSITION": "拆解",
    "SPATIAL_SEARCH": "搜索",
    "INVERSE_PARAMETER_INFERENCE": "反推",
    "PREDICT_OBSERVE_EXPLAIN": "预测",
    "BOUNDARY_CONDITION_COMPARISON": "边界",
    "LIMIT_AND_APPROXIMATION_CHECK": "极限",
    "MODEL_BREAKDOWN_COUNTEREXAMPLE": "失效",
    "TRANSIENT_PROCESS": "瞬态",
    "FREQUENCY_RESPONSE": "频响",
    "MEASUREMENT_METHOD_COMPARISON": "测量",
    "DESIGN_OPTIMIZATION": "优化",
    "MULTI_SOLUTION_COMPARISON": "多方案",
}


def _pattern_by_id(pattern_id: str) -> dict[str, Any] | None:
    return next(
        (
            pattern
            for pattern in KNOWLEDGE.public_experiment_design_patterns()
            if pattern.get("pattern_id") == pattern_id
        ),
        None,
    )


def _selected_formula_rows(selection: dict[str, Any]) -> list[dict[str, Any]]:
    primary_ids = list(selection.get("primary_formula_ids", []))
    supporting_ids = list(selection.get("supporting_formula_ids", []))
    selected_profile_ids = list(
        dict.fromkeys(
            [
                *selection.get("primary_profile_ids", []),
                *selection.get("supporting_profile_ids", []),
            ]
        )
    )
    profiles = [
        profile
        for profile_id in selected_profile_ids
        for profile in [_profile_by_id(str(profile_id))]
        if profile is not None
    ]
    rows: list[dict[str, Any]] = []
    for formula_id in dict.fromkeys([*primary_ids, *supporting_ids]):
        matching_profiles = [
            item
            for item in profiles
            if formula_id
            in {
                *item.get("primary_formula_ids", []),
                *item.get("supporting_formula_ids", []),
            }
        ]
        if not matching_profiles:
            continue
        profile_ids = [str(item.get("profile_id") or "") for item in matching_profiles]
        applicable = {
            str(pattern_id)
            for item in matching_profiles
            for pattern_id in item.get("applicable_experiment_pattern_ids", [])
        }
        rows.append(
            {
                "formula_id": formula_id,
                "formula": _formula_display(formula_id),
                "role": "PRIMARY" if formula_id in primary_ids else "SUPPORTING",
                "profile_id": profile_ids[0],
                "profile_ids": profile_ids,
                "profile_title": " / ".join(
                    str(item.get("title_zh") or "") for item in matching_profiles
                ),
                "applicable_pattern_ids": [
                    str(pattern.get("pattern_id") or "")
                    for pattern in KNOWLEDGE.public_experiment_design_patterns()
                    if pattern.get("pattern_id") in applicable
                ],
            }
        )
    return rows


def _coverage_matrix(selection: dict[str, Any]) -> dict[str, Any]:
    patterns = KNOWLEDGE.public_experiment_design_patterns()
    return {
        "columns": [
            {
                "pattern_id": pattern.get("pattern_id"),
                "title": pattern.get("title_zh"),
                "short_title": _PATTERN_SHORT_TITLES.get(
                    str(pattern.get("pattern_id") or ""),
                    str(pattern.get("title_zh") or ""),
                ),
            }
            for pattern in patterns
        ],
        "rows": _selected_formula_rows(selection),
    }


def _profile_values(profile_ids: Iterable[str], field: str) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for profile_id in profile_ids
            for profile in [_profile_by_id(str(profile_id))]
            if profile is not None
            for value in _quantity_labels(profile.get(field))
        )
    )


def _generate_experiment_methods(
    flow: dict[str, Any],
    *,
    strategy: str,
) -> list[dict[str, Any]]:
    """Generate methods from formula capabilities and finite design patterns.

    No catalog scene is sampled here. Each method is composed at runtime from
    the confirmed formula rows, their declared pattern support and the
    student's semantic topic analysis.
    """

    selection = flow.get("formula_selection", {})
    analysis = flow.get("topic_analysis", {})
    formula_rows = _selected_formula_rows(selection)
    if not formula_rows:
        return []
    profile_ids = list(
        dict.fromkeys(
            [
                *selection.get("primary_profile_ids", []),
                *selection.get("supporting_profile_ids", []),
            ]
        )
    )
    profile_changed = _profile_values(profile_ids, "supported_variations")
    profile_observed = _profile_values(profile_ids, "supported_observations")
    all_changed = list(
        dict.fromkeys([*_unique_text(analysis.get("changed_quantities")), *profile_changed])
    )[:5]
    all_observed = list(
        dict.fromkeys([*_unique_text(analysis.get("observed_quantities")), *profile_observed])
    )[:5]
    all_boundaries = _profile_values(profile_ids, "boundary_conditions")[:6]
    objects = _unique_text(analysis.get("mentioned_objects")) or [
        "与已确认公式对应的场源、材料或边界对象",
        "可移动测量探针",
    ]
    supported_sets = [set(row.get("applicable_pattern_ids", [])) for row in formula_rows]
    if strategy in {"SINGLE", "COMBINED"}:
        pattern_ids = set.intersection(*supported_sets) if supported_sets else set()
    else:
        pattern_ids = set.union(*supported_sets) if supported_sets else set()
    ordered_pattern_ids = [
        str(pattern.get("pattern_id") or "")
        for pattern in KNOWLEDGE.public_experiment_design_patterns()
        if pattern.get("pattern_id") in pattern_ids
    ]
    methods: list[dict[str, Any]] = []
    for index, pattern_id in enumerate(ordered_pattern_ids, start=1):
        pattern = _pattern_by_id(pattern_id)
        if pattern is None:
            continue
        if strategy == "SEPARATE_THEN_COMBINE":
            assignments = []
            for row in formula_rows:
                supported = list(row.get("applicable_pattern_ids", []))
                assigned = pattern_id if pattern_id in supported else (supported[0] if supported else None)
                if assigned:
                    assignments.append(
                        {
                            "formula_id": row["formula_id"],
                            "pattern_id": assigned,
                        }
                    )
            assignment_text = "；".join(
                f"先用{_pattern_by_id(item['pattern_id']).get('title_zh')}研究"
                f"{_formula_display(item['formula_id'])}"
                for item in assignments
                if _pattern_by_id(item["pattern_id"]) is not None
            )
            process_prefix = f"{assignment_text}；最后把各小实验放回同一 VR 任务中交叉验证。"
            title = f"分步组合｜以{pattern.get('title_zh')}为主"
        else:
            assignments = [
                {"formula_id": row["formula_id"], "pattern_id": pattern_id}
                for row in formula_rows
            ]
            process_prefix = "在同一 VR 实验中联合使用已确认公式。"
            title = f"组合实验｜{pattern.get('title_zh')}"
        if pattern_id in {
            "SINGLE_PARAMETER_SWEEP",
            "PREDICT_OBSERVE_EXPLAIN",
            "LIMIT_AND_APPROXIMATION_CHECK",
        }:
            method_changed = all_changed[:1]
        elif pattern_id == "CONTROLLED_COMPARISON":
            method_changed = all_changed[1:2] or all_changed[:1]
        elif pattern_id == "INVERSE_PARAMETER_INFERENCE":
            method_changed = profile_changed[:1] or all_changed[:1]
        else:
            method_changed = all_changed[:3]
        changed_text = "、".join(method_changed) or "公式中的主要输入量"
        observed_text = "、".join(all_observed[:3]) or "公式给出的目标响应"
        boundary_text = (
            "；".join(all_boundaries[:1]).rstrip("。；;,.，")
            or "课程公式适用的边界条件"
        )
        process = str(pattern.get("method_template") or "").format(
            changed=changed_text,
            observed=observed_text,
            boundary=boundary_text,
        )
        method_id = f"EMVR-METHOD-{strategy}-{index:02d}-{pattern_id}"
        methods.append(
            {
                "method_id": method_id,
                "option_id": f"emvr-method:{method_id}",
                "title": title,
                "pattern_ids": list(dict.fromkeys(item["pattern_id"] for item in assignments)),
                "formula_pattern_assignments": assignments,
                "primary_formula_ids": list(selection.get("primary_formula_ids", [])),
                "supporting_formula_ids": list(selection.get("supporting_formula_ids", [])),
                "description": (
                    f"采用“{pattern.get('design_logic')}”的组织方式，"
                    f"研究{'、'.join(row['formula'] for row in formula_rows)}。"
                ),
                "process_summary": f"{process_prefix} {process}",
                "objects": objects,
                "operations": [str(pattern.get("design_logic") or "")],
                "changed_quantities": method_changed,
                "observed_quantities": all_observed,
                "required_boundary_conditions": all_boundaries,
            }
        )
    return methods


def _format_coverage_and_methods(
    matrix: dict[str, Any],
    methods: list[dict[str, Any]],
) -> str:
    columns = matrix.get("columns", [])
    header = "|公式|" + "|".join(str(item.get("short_title") or "") for item in columns) + "|"
    divider = "|---|" + "|".join("---" for _ in columns) + "|"
    rows = [header, divider]
    for row in matrix.get("rows", []):
        applicable = set(row.get("applicable_pattern_ids", []))
        rows.append(
            f"|{row.get('formula')}|"
            + "|".join("✓" if item.get("pattern_id") in applicable else "—" for item in columns)
            + "|"
        )
    parts = [
        "下面的覆盖矩阵来自已确认公式的适用能力；✓ 表示这类公式适合采用该实验形式，— 表示不强行套用。",
        "\n".join(rows),
        "我据此实时生成了下面这些实验方法，它们不是从固定图景库抽取的：",
    ]
    for index, method in enumerate(methods, start=1):
        pattern_names = [
            str(pattern.get("title_zh") or "")
            for pattern_id in method.get("pattern_ids", [])
            for pattern in [_pattern_by_id(str(pattern_id))]
            if pattern is not None
        ]
        parts.append(
            f"方法 {index}｜{method.get('title')}\n"
            f"实验形式：{'、'.join(pattern_names)}\n"
            f"设计思路：{method.get('description')}\n"
            f"简要过程：{method.get('process_summary')}"
        )
    parts.append(
        "你可以选择一种方法，也可以组合几种方法；选定后我会把它整理成完整实验方向，再请你审阅对象、操作、变化量和观察量。"
    )
    return "\n\n".join(parts)


def _selected_action(turn_intent: dict[str, Any], act_type: str) -> dict[str, Any] | None:
    updates = turn_intent.get("semantic_updates", {})
    actions = updates.get("emvr_formula_actions", []) if isinstance(updates, dict) else []
    raw = next(
        (
            deepcopy(item.get("content"))
            for item in actions
            if isinstance(item, dict)
            and item.get("type") == act_type
            and isinstance(item.get("content"), dict)
        ),
        None,
    )
    return normalize_formula_flow_action(act_type, raw)


def _semantic_service_failed(turn_intent: dict[str, Any]) -> bool:
    """Identify an explicit resolver outage without interpreting user words."""

    return str(turn_intent.get("source") or "").startswith(
        "SEMANTIC_SERVICE_FALLBACK"
    )


def _remember_semantic_recovery(
    flow: dict[str, Any],
    *,
    phase: str,
    message: str,
    turn_intent: dict[str, Any],
) -> None:
    """Retain unresolved turns for a later semantic pass, never as design data."""

    text = message.strip()[:2400]
    existing = flow.get("semantic_recovery")
    if not isinstance(existing, dict) or existing.get("phase") != phase:
        existing = {"phase": phase, "messages": []}
    messages = existing.get("messages", [])
    messages = list(messages) if isinstance(messages, list) else []
    if text and text not in messages:
        messages.append(text)
    existing["messages"] = messages[-4:]
    existing["resolver_source"] = str(turn_intent.get("source") or "")[:160]
    flow["semantic_recovery"] = existing


def _clear_semantic_recovery(flow: dict[str, Any]) -> None:
    flow.pop("semantic_recovery", None)


def _formula_selection_from_option(option_id: str | None) -> dict[str, Any] | None:
    prefix = "emvr-formula:"
    if not isinstance(option_id, str) or not option_id.startswith(prefix):
        return None
    profile_id = option_id[len(prefix):]
    if profile_id not in _PROFILE_IDS:
        return None
    return {"primary_profile_ids": [profile_id], "supporting_profile_ids": [], "student_rationale": None}


def _formula_selection_from_visible_card_reference(
    message: str,
    flow: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve one exact reference to the currently visible formula cards.

    This is an outage-safe equivalent of clicking a formula-card button.  It
    compares only the complete labels carried by the current state and never
    searches a topic keyword list or infers a formula from physics words.
    """

    compact_message = "".join(message.split()).casefold()
    if not compact_message:
        return None
    candidates = set(
        str(item)
        for item in flow.get("formula_selection", {}).get(
            "candidate_profile_ids", []
        )
        if str(item)
    )
    matches = [
        str(card.get("profile_id") or "")
        for card in flow.get("formula_cards", [])
        if isinstance(card, dict)
        and str(card.get("profile_id") or "") in candidates
        and (title := "".join(str(card.get("title") or "").split()).casefold())
        and title in compact_message
    ]
    matches = list(dict.fromkeys(item for item in matches if item))
    if len(matches) != 1:
        return None
    return {
        "primary_profile_ids": matches,
        "supporting_profile_ids": [],
        "student_rationale": message.strip()[:1000] or None,
    }


def _semantic_outage_stage_payload(
    flow: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    """Keep deterministic UI actions available while semantics are offline."""

    payload: dict[str, Any] = {
        "emvr_formula_phase": phase,
        "semantic_recovery_pending": True,
        "preserve_pending_action": True,
    }
    if phase == FORMULA_CANDIDATES_PRESENTED:
        payload["formula_cards"] = deepcopy(flow.get("formula_cards", []))
    elif phase == FORMULA_COMPOSITION_REVIEW:
        payload["composition_options"] = [
            {
                "option_id": "emvr-composition:combined",
                "label": "组合成一个完整实验",
            },
            {
                "option_id": "emvr-composition:separate_then_combine",
                "label": "逐个小实验后组合",
            },
        ]
        payload["confirmed_formula_selection"] = deepcopy(
            flow.get("formula_selection", {})
        )
    elif phase == EXPERIMENT_METHODS_PRESENTED:
        payload["coverage_matrix"] = deepcopy(flow.get("coverage_matrix", {}))
        payload["experiment_methods"] = deepcopy(
            flow.get("experiment_methods", [])
        )
    elif phase == EXPERIMENT_DIRECTION_REVIEW:
        payload["experiment_brief_draft"] = deepcopy(
            flow.get("experiment_brief", {})
        )
    return payload


def _composition_from_option(option_id: str | None) -> dict[str, Any] | None:
    prefix = "emvr-composition:"
    if not isinstance(option_id, str) or not option_id.startswith(prefix):
        return None
    strategy = option_id[len(prefix):].upper()
    if strategy not in _COMPOSITION_STRATEGIES:
        return None
    return {"strategy": strategy, "student_rationale": None}


def _method_selection_from_option(
    option_id: str | None,
    candidate_method_ids: set[str],
) -> dict[str, Any] | None:
    prefix = "emvr-method:"
    if not isinstance(option_id, str) or not option_id.startswith(prefix):
        return None
    method_id = option_id[len(prefix):]
    if method_id not in candidate_method_ids:
        return None
    return {"selected_method_ids": [method_id]}


def _build_experiment_brief(
    flow: dict[str, Any],
    method_choice: dict[str, Any],
) -> dict[str, Any]:
    analysis = flow.get("topic_analysis", {})
    formula_selection = flow.get("formula_selection", {})
    candidate_methods = {
        str(method.get("method_id") or ""): method
        for method in flow.get("experiment_methods", [])
        if isinstance(method, dict)
    }
    selected_methods = [
        candidate_methods[method_id]
        for method_id in method_choice.get("selected_method_ids", [])
        if method_id in candidate_methods
    ]
    changed = _unique_text(method_choice.get("changed_quantities")) or _unique_text(
        analysis.get("changed_quantities")
    ) or list(
        dict.fromkeys(
            item
            for method in selected_methods
            for item in method.get("changed_quantities", [])
        )
    )[:5]
    observed = _unique_text(method_choice.get("observed_quantities")) or _unique_text(
        analysis.get("observed_quantities")
    ) or list(
        dict.fromkeys(
            item
            for method in selected_methods
            for item in method.get("observed_quantities", [])
        )
    )[:5]
    comparisons = _unique_text(method_choice.get("comparison_cases"))
    objects = _unique_text(method_choice.get("objects")) or _unique_text(
        analysis.get("mentioned_objects")
    )
    if not objects:
        objects = list(
            dict.fromkeys(
                item
                for method in selected_methods
                for item in method.get("objects", [])
            )
        )
    operations = _unique_text(method_choice.get("operations"))
    if not operations:
        operations = [
            str(method.get("process_summary") or "")
            for method in selected_methods
            if str(method.get("process_summary") or "").strip()
        ]
    boundaries = _unique_text(method_choice.get("boundary_conditions")) or list(
        dict.fromkeys(
            item
            for method in selected_methods
            for item in method.get("required_boundary_conditions", [])
        )
    )[:6]
    custom = str(method_choice.get("custom_direction") or "").strip()
    original_topic = str(analysis.get("topic_description") or "").strip()
    if custom:
        topic = custom
    elif analysis.get("specificity") == "BROAD" and selected_methods:
        topic = "；".join(
            dict.fromkeys(
                str(method.get("title") or "").strip() for method in selected_methods
            )
        )
    else:
        topic = original_topic
    return {
        "topic": topic,
        "primary_formula_ids": list(formula_selection.get("primary_formula_ids", [])),
        "supporting_formula_ids": list(formula_selection.get("supporting_formula_ids", [])),
        "formula_composition_strategy": str(
            flow.get("formula_composition", {}).get("strategy") or "SINGLE"
        ),
        "selected_experiment_method_ids": [
            str(method.get("method_id")) for method in selected_methods
        ],
        "selected_experiment_pattern_ids": list(
            dict.fromkeys(
                pattern_id
                for method in selected_methods
                for pattern_id in method.get("pattern_ids", [])
            )
        ),
        "objects": objects,
        "operations": operations,
        "changed_quantities": changed,
        "observed_quantities": observed,
        "comparison_cases": comparisons,
        "boundary_conditions": boundaries,
    }


def _brief_summary(brief: dict[str, Any]) -> str:
    comparison_text = (
        f"，并比较{'、'.join(brief.get('comparison_cases', []))}"
        if brief.get("comparison_cases")
        else ""
    )
    return (
        f"围绕“{brief.get('topic')}”，使用{'、'.join(brief.get('objects', []))}，"
        f"通过{'、'.join(brief.get('operations', []))}改变{'、'.join(brief.get('changed_quantities', []))}，"
        f"观察{'、'.join(brief.get('observed_quantities', []))}{comparison_text}。"
    )


def _commit_brief(session: DesignSession, flow: dict[str, Any]) -> None:
    brief = deepcopy(flow.get("experiment_brief", {}))
    if not isinstance(brief, dict):
        return
    emvr = session.design_context.setdefault("emvr_design", {})
    field_state = emvr.setdefault("field_state", {})
    summary = _brief_summary(brief)
    formula_titles = [
        str(profile.get("title_zh") or "")
        for profile_id in [
            *flow["formula_selection"].get("primary_profile_ids", []),
            *flow["formula_selection"].get("supporting_profile_ids", []),
        ]
        for profile in [_profile_by_id(profile_id)]
        if profile is not None and str(profile.get("title_zh") or "")
    ]
    field_state.update(
        {
            "experiment_brief": summary,
            "direction_summary": str(brief.get("topic") or ""),
            "research_summary": str(brief.get("topic") or ""),
            "research_object": "、".join(brief.get("objects", [])),
            "course_relationship": "；".join(dict.fromkeys(formula_titles)),
            "required_behaviors": list(brief.get("operations", [])),
            "changed_quantities": list(brief.get("changed_quantities", [])),
            "observed_quantities": list(brief.get("observed_quantities", [])),
            "comparison_cases": list(brief.get("comparison_cases", [])),
            "object_constraints": list(brief.get("boundary_conditions", [])),
        }
    )
    emvr.update(
        {
            "awaiting_new_topic": False,
            "experiment_brief": summary,
            "brief": summary,
            "current_brief": summary,
            "brief_source": "CONFIRMED_FORMULA_FLOW",
            "authoritative_experiment_brief": brief,
            "selected_primary_formula_ids": list(brief.get("primary_formula_ids", [])),
            "selected_supporting_formula_ids": list(brief.get("supporting_formula_ids", [])),
        }
    )
    idea = session.design_context.setdefault("idea", {})
    if isinstance(idea, dict):
        idea["original"] = str(flow.get("topic_analysis", {}).get("topic_description") or "")


def handle_emvr_formula_turn(
    session: DesignSession,
    message: str,
    turn_intent: dict[str, Any],
    *,
    selected_option_id: str | None = None,
    complete_stage: bool = False,
) -> tuple[StepOutput, bool]:
    """Execute the EMVR-only topic/formula/scene/direction state machine."""

    flow = ensure_emvr_formula_flow(session)
    phase = str(flow.get("phase") or TOPIC_RECEIVED)
    outage_formula_selection: dict[str, Any] | None = None
    if _semantic_service_failed(turn_intent):
        _remember_semantic_recovery(
            flow,
            phase=phase,
            message=message,
            turn_intent=turn_intent,
        )
        if phase == FORMULA_CANDIDATES_PRESENTED:
            outage_formula_selection = _formula_selection_from_visible_card_reference(
                message,
                flow,
            )
        if outage_formula_selection is None:
            return (
                StepOutput(
                    assistant_message=(
                        "这次课程理解服务没有完成解析，所以我没有把你的话误写进实验设计。"
                        "当前公式、方法和进度都已保留；如果页面下方仍有选项，可以直接点击继续，"
                        "不需要重新输入整段内容，也不需要重新开始。"
                    ),
                    stage_payload=_semantic_outage_stage_payload(flow, phase),
                    student_task=(
                        "可以直接点击当前选项继续；若需要自由修改，稍后再重试刚才的内容。"
                    ),
                ),
                False,
            )
    topic_action = _selected_action(turn_intent, "SET_EMVR_TOPIC")
    if topic_action:
        _clear_semantic_recovery(flow)
        _reset_formula_choices(flow)
        flow["topic_analysis"] = topic_action
        flow["phase"] = TOPIC_RECEIVED
        phase = TOPIC_RECEIVED
    formula_revision = _selected_action(turn_intent, "SELECT_EMVR_FORMULAS")
    if (
        phase
        in {
            FORMULA_COMPOSITION_REVIEW,
            EXPERIMENT_METHODS_PRESENTED,
            EXPERIMENT_DIRECTION_REVIEW,
        }
        and formula_revision
    ):
        available_profiles = [
            str(card.get("profile_id") or "")
            for card in flow.get("formula_cards", [])
            if isinstance(card, dict) and str(card.get("profile_id") or "")
        ]
        if available_profiles:
            _clear_semantic_recovery(flow)
            flow["formula_selection"]["candidate_profile_ids"] = available_profiles
            flow["formula_composition"] = {"strategy": None, "status": "PENDING"}
            flow["method_selection"] = {
                "candidate_method_ids": [],
                "selected_method_ids": [],
                "selection_status": "PENDING",
            }
            for key in ("experiment_methods", "coverage_matrix", "experiment_brief"):
                flow.pop(key, None)
            flow["phase"] = FORMULA_CANDIDATES_PRESENTED
            phase = FORMULA_CANDIDATES_PRESENTED
    composition_revision = _selected_action(
        turn_intent, "SET_EMVR_FORMULA_COMPOSITION"
    )
    if (
        phase
        in {
            FORMULA_COMPOSITION_REVIEW,
            EXPERIMENT_METHODS_PRESENTED,
            EXPERIMENT_DIRECTION_REVIEW,
        }
        and composition_revision
        and composition_revision.get("strategy")
        in {"COMBINED", "SEPARATE_THEN_COMBINE"}
    ):
        _clear_semantic_recovery(flow)
        flow["formula_composition"] = {
            "strategy": composition_revision["strategy"],
            "status": "CONFIRMED",
            "student_rationale": composition_revision.get("student_rationale"),
        }
        flow["method_selection"] = {
            "candidate_method_ids": [],
            "selected_method_ids": [],
            "selection_status": "PENDING",
        }
        flow.pop("experiment_methods", None)
        flow.pop("coverage_matrix", None)
        flow.pop("experiment_brief", None)
        flow["phase"] = FORMULA_COMPOSITION_REVIEW
        phase = FORMULA_COMPOSITION_REVIEW

    if phase == TOPIC_RECEIVED:
        analysis = flow.get("topic_analysis")
        if not isinstance(analysis, dict):
            topic_seed = str(flow.get("topic_seed") or "").strip()
            if topic_seed:
                introduction = (
                    f"从引导模式带来的研究方向是“{topic_seed}”。这部分没有丢失，但还没有被当成"
                    "已确认公式或完整 EMVR 方案。请确认沿用这个方向，或直接补充你想改变和观察的量；"
                    "我会据此匹配课程公式。"
                )
                question = "沿用这个研究方向检索课程公式，还是先修改方向？"
            else:
                introduction = (
                    "我先不替你填写实验对象、变量和流程。请用一两句话说明想研究的 ECE329 主题；"
                    "如果已经有想验证的公式、想改变的量或想观察的现象，也可以一起写出来。"
                )
                question = "你想研究哪个ECE329主题，或想验证哪条公式？"
            return (
                StepOutput(
                    assistant_message=introduction,
                    stage_payload={
                        "emvr_formula_phase": TOPIC_RECEIVED,
                        "awaiting_user_design_input": True,
                        "pending_action": {
                            "type": "ANSWER_EMVR_FORMULA_TOPIC",
                            "subject": "emvr_formula_topic",
                            "question": question,
                        },
                    },
                    student_task=question,
                ),
                False,
            )
        profiles = score_formula_profiles(analysis, limit=4)
        if not profiles:
            return (
                StepOutput(
                    assistant_message=(
                        "这个主题目前还没有和一条可验证的 ECE329 公式建立可靠联系，所以我不会随意填入相邻公式。"
                        "请再补充你想改变的量或想观察的结果，我会据此缩小理论范围。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": TOPIC_RECEIVED,
                        "awaiting_user_design_input": True,
                        "pending_action": {
                            "type": "ANSWER_EMVR_FORMULA_TOPIC",
                            "subject": "emvr_formula_topic",
                            "question": "你最想主动改变什么，并观察什么结果？",
                        },
                    },
                    student_task="你最想主动改变什么，并观察什么结果？",
                ),
                False,
            )
        cards = _formula_cards(profiles)
        selection = flow["formula_selection"]
        selection["candidate_profile_ids"] = [card["profile_id"] for card in cards]
        selection["selection_status"] = "PENDING"
        flow["formula_cards"] = cards
        flow["phase"] = FORMULA_CANDIDATES_PRESENTED
        return (
            StepOutput(
                assistant_message=_format_formula_cards(
                    cards, str(analysis.get("topic_description") or "当前主题")
                ),
                stage_payload={
                    "emvr_formula_phase": FORMULA_CANDIDATES_PRESENTED,
                    "formula_cards": deepcopy(cards),
                    "pending_action": {
                        "type": "SELECT_EMVR_FORMULA",
                        "subject": "formula_selection",
                        "proposal": {"candidate_profile_ids": selection["candidate_profile_ids"]},
                        "question": "你希望采用哪组公式，或怎样组合主要公式与辅助公式？",
                    },
                },
                student_task="你希望采用哪组公式，或怎样组合主要公式与辅助公式？",
            ),
            False,
        )

    if phase == FORMULA_CANDIDATES_PRESENTED:
        chosen = (
            _formula_selection_from_option(selected_option_id)
            or _selected_action(turn_intent, "SELECT_EMVR_FORMULAS")
            or outage_formula_selection
        )
        candidates = set(flow["formula_selection"].get("candidate_profile_ids", []))
        if chosen:
            primary_profiles = [
                item for item in chosen.get("primary_profile_ids", []) if item in candidates
            ]
            supporting_profiles = [
                item
                for item in chosen.get("supporting_profile_ids", [])
                if item in candidates and item not in primary_profiles
            ]
            if primary_profiles:
                _clear_semantic_recovery(flow)
                candidate_profiles = [
                    profile
                    for profile_id in candidates
                    for profile in [_profile_by_id(profile_id)]
                    if profile is not None
                ]
                allowed_formula_ids = {
                    formula_id
                    for profile in candidate_profiles
                    for formula_id in [
                        *profile.get("primary_formula_ids", []),
                        *profile.get("supporting_formula_ids", []),
                    ]
                }
                default_primary, _ = _formula_ids_for_profiles(primary_profiles)
                default_supporting, _ = _formula_ids_for_profiles(supporting_profiles)
                primary_formula_ids = [
                    item
                    for item in chosen.get("primary_formula_ids", [])
                    if item in allowed_formula_ids
                ] or default_primary
                support = [
                    item
                    for item in chosen.get("supporting_formula_ids", [])
                    if item in allowed_formula_ids and item not in primary_formula_ids
                ] or [
                    item for item in default_supporting if item not in primary_formula_ids
                ]
                selection = flow["formula_selection"]
                selection.update(
                    {
                        "candidate_profile_ids": [
                            str(card.get("profile_id") or "")
                            for card in flow.get("formula_cards", [])
                            if isinstance(card, dict)
                            and str(card.get("profile_id") or "") in candidates
                        ],
                        "primary_profile_ids": primary_profiles,
                        "supporting_profile_ids": supporting_profiles,
                        "primary_formula_ids": primary_formula_ids,
                        "supporting_formula_ids": support,
                        "selection_status": "CONFIRMED",
                        "student_rationale": chosen.get("student_rationale"),
                    }
                )
                flow["phase"] = FORMULA_SELECTION_CONFIRMED
                phase = FORMULA_SELECTION_CONFIRMED
        if phase == FORMULA_CANDIDATES_PRESENTED:
            return (
                StepOutput(
                    assistant_message=(
                        "公式候选仍然只是参考，还没有写入正式设计。你可以选一组作为主线，"
                        "也可以说明哪组作为主要公式、哪组只用于辅助比较。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": FORMULA_CANDIDATES_PRESENTED,
                        "formula_cards": deepcopy(flow.get("formula_cards", [])),
                        "preserve_pending_action": True,
                    },
                    student_task="你想把哪组公式作为实验主线？",
                ),
                False,
            )

    if phase == FORMULA_SELECTION_CONFIRMED:
        selected_formula_ids = list(
            dict.fromkeys(
                [
                    *flow["formula_selection"].get("primary_formula_ids", []),
                    *flow["formula_selection"].get("supporting_formula_ids", []),
                ]
            )
        )
        if len(selected_formula_ids) > 1:
            composition_action = _selected_action(
                turn_intent, "SET_EMVR_FORMULA_COMPOSITION"
            )
            if composition_action and composition_action.get("strategy") in {
                "COMBINED",
                "SEPARATE_THEN_COMBINE",
            }:
                _clear_semantic_recovery(flow)
                flow["formula_composition"] = {
                    "strategy": composition_action["strategy"],
                    "status": "CONFIRMED",
                    "student_rationale": composition_action.get("student_rationale"),
                }
                flow["phase"] = FORMULA_COMPOSITION_REVIEW
                phase = FORMULA_COMPOSITION_REVIEW
            else:
                flow["phase"] = FORMULA_COMPOSITION_REVIEW
                return (
                    StepOutput(
                        assistant_message=(
                            "你已经确定了多条公式。接下来可以采用两种组织方式：\n\n"
                            "1. 组合公式设计一个完整实验：让多条公式共同解释同一组操作和观察结果。\n"
                            "2. 先为每条公式设计一个小实验，再把它们组合成连续任务。\n\n"
                            "你希望采用哪一种？如果你有自己的组合关系，也可以直接说明。"
                        ),
                        stage_payload={
                            "emvr_formula_phase": FORMULA_COMPOSITION_REVIEW,
                            "composition_options": [
                                {
                                    "option_id": "emvr-composition:combined",
                                    "label": "组合成一个完整实验",
                                },
                                {
                                    "option_id": "emvr-composition:separate_then_combine",
                                    "label": "逐个小实验后组合",
                                },
                            ],
                            "confirmed_formula_selection": deepcopy(flow["formula_selection"]),
                            "pending_action": {
                                "type": "SELECT_EMVR_FORMULA_COMPOSITION",
                                "subject": "formula_composition",
                                "question": "这些公式要在同一个实验中联合使用，还是先分别设计再组合？",
                            },
                        },
                        student_task="这些公式要在同一个实验中联合使用，还是先分别设计再组合？",
                    ),
                    False,
                )
        else:
            flow["formula_composition"] = {
                "strategy": "SINGLE",
                "status": "CONFIRMED",
            }
            phase = FORMULA_COMPOSITION_REVIEW

    if phase == FORMULA_COMPOSITION_REVIEW:
        composition = flow.get("formula_composition", {})
        if composition.get("status") != "CONFIRMED":
            choice = (
                _composition_from_option(selected_option_id)
                or _selected_action(turn_intent, "SET_EMVR_FORMULA_COMPOSITION")
            )
            if choice and choice.get("strategy") in {"COMBINED", "SEPARATE_THEN_COMBINE"}:
                _clear_semantic_recovery(flow)
                flow["formula_composition"] = {
                    "strategy": choice["strategy"],
                    "status": "CONFIRMED",
                    "student_rationale": choice.get("student_rationale"),
                }
                composition = flow["formula_composition"]
        if composition.get("status") != "CONFIRMED":
            return (
                StepOutput(
                    assistant_message=(
                        "公式选择已经保留。请决定把它们放在同一个完整实验中联合使用，"
                        "还是先分别设计小实验再组合；我会按照你的决定生成覆盖矩阵。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": FORMULA_COMPOSITION_REVIEW,
                        "composition_options": [
                            {"option_id": "emvr-composition:combined", "label": "组合成一个完整实验"},
                            {"option_id": "emvr-composition:separate_then_combine", "label": "逐个小实验后组合"},
                        ],
                        "preserve_pending_action": True,
                    },
                    student_task="这些公式采用哪种组织方式？",
                ),
                False,
            )
        strategy = str(composition.get("strategy") or "SINGLE")
        matrix = _coverage_matrix(flow["formula_selection"])
        methods = _generate_experiment_methods(flow, strategy=strategy)
        if not methods:
            return (
                StepOutput(
                    assistant_message=(
                        "公式已经确认，但目前没有一类实验形式同时满足这些公式的适用能力。"
                        "我保留公式选择，请把组织方式改为“逐个小实验后组合”，或调整公式组合。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": FORMULA_COMPOSITION_REVIEW,
                        "coverage_matrix": matrix,
                    },
                    student_task="需要改为逐个小实验后组合，还是调整公式？",
                ),
                False,
            )
        flow["coverage_matrix"] = matrix
        flow["experiment_methods"] = methods
        flow["method_selection"]["candidate_method_ids"] = [
            method["method_id"] for method in methods
        ]
        flow["method_selection"]["selection_status"] = "PENDING"
        flow["phase"] = EXPERIMENT_METHODS_PRESENTED
        return (
            StepOutput(
                assistant_message=_format_coverage_and_methods(matrix, methods),
                stage_payload={
                    "emvr_formula_phase": EXPERIMENT_METHODS_PRESENTED,
                    "coverage_matrix": deepcopy(matrix),
                    "experiment_methods": deepcopy(methods),
                    "confirmed_formula_selection": deepcopy(flow["formula_selection"]),
                    "pending_action": {
                        "type": "SELECT_EMVR_EXPERIMENT_METHOD",
                        "subject": "experiment_method_selection",
                        "proposal": {
                            "candidate_method_ids": flow["method_selection"]["candidate_method_ids"]
                        },
                        "question": "你希望采用或组合哪些实验方法？",
                    },
                },
                student_task="你希望采用或组合哪些实验方法？",
            ),
            False,
        )

    if phase == EXPERIMENT_METHODS_PRESENTED:
        candidates = set(flow["method_selection"].get("candidate_method_ids", []))
        method_choice = (
            _method_selection_from_option(selected_option_id, candidates)
            or _selected_action(turn_intent, "SELECT_EMVR_EXPERIMENT_METHODS")
        )
        if method_choice:
            method_choice["selected_method_ids"] = [
                item
                for item in method_choice.get("selected_method_ids", [])
                if item in candidates
            ]
        if not method_choice or not method_choice.get("selected_method_ids"):
            return (
                StepOutput(
                    assistant_message=(
                        "公式、组织方式和覆盖矩阵都已保留。你可以选择一种方法，也可以把几种方法组合起来；"
                        "如果需要改造其中的方法，请同时说明希望保留的操作和观察方式。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": EXPERIMENT_METHODS_PRESENTED,
                        "coverage_matrix": deepcopy(flow.get("coverage_matrix", {})),
                        "experiment_methods": deepcopy(flow.get("experiment_methods", [])),
                        "preserve_pending_action": True,
                    },
                    student_task="你想采用或组合哪些实验方法？",
                ),
                False,
            )
        _clear_semantic_recovery(flow)
        brief = _build_experiment_brief(flow, method_choice)
        flow["method_selection"].update(
            {
                "selected_method_ids": list(method_choice["selected_method_ids"]),
                "selection_status": "SELECTED",
            }
        )
        flow["experiment_brief"] = brief
        flow["phase"] = EXPERIMENT_DIRECTION_REVIEW
        return (
            StepOutput(
                assistant_message=(
                    "我已经按你确认的公式和实验方法整理出实验方向草稿：\n\n"
                    f"{_brief_summary(brief)}\n\n"
                    f"主要公式：{'；'.join(_formula_display(item) for item in brief['primary_formula_ids'])}。\n"
                    f"辅助公式：{'；'.join(_formula_display(item) for item in brief['supporting_formula_ids']) or '暂不设置'}。\n"
                    f"模型边界：{'；'.join(brief['boundary_conditions'])}。\n\n"
                    "请重点检查研究对象、主动变化量和观察量是否符合你的想法；这里确认后，实验方向才会锁定并进入后续 EMVR 细化。"
                ),
                stage_payload={
                    "emvr_formula_phase": EXPERIMENT_DIRECTION_REVIEW,
                    "experiment_brief_draft": deepcopy(brief),
                    "pending_action": {
                        "type": "CONFIRM_EMVR_FORMULA_DIRECTION",
                        "subject": "experiment_brief",
                        "proposal": deepcopy(brief),
                        "advance_on_accept": True,
                        "question": "这份实验方向是否准确；如需调整，请直接指出具体部分。",
                    },
                },
                student_task="这份实验方向是否准确；如需调整，请直接指出具体部分。",
            ),
            False,
        )

    if phase == EXPERIMENT_DIRECTION_REVIEW:
        revise_action = _selected_action(turn_intent, "REVISE_EMVR_DIRECTION")
        lock_action = _selected_action(turn_intent, "LOCK_EMVR_DIRECTION")
        semantic_updates = turn_intent.get("semantic_updates", {})
        controls = set(semantic_updates.get("control_actions", [])) \
            if isinstance(semantic_updates, dict) and isinstance(semantic_updates.get("control_actions"), list) \
            else set()
        if revise_action is not None:
            _clear_semantic_recovery(flow)
            brief = flow.get("experiment_brief", {})
            _apply_brief_updates(brief, revise_action.get("brief_updates"))
            if not (
                lock_action is not None
                or complete_stage
                or controls.intersection({"ACCEPT", "ADVANCE"})
            ):
                return (
                    StepOutput(
                        assistant_message=(
                            "已经只调整你点名的部分，主要公式和其余设计保持不变。更新后的实验方向是：\n\n"
                            f"{_brief_summary(brief)}\n\n请再看一下这版是否准确。"
                        ),
                        stage_payload={
                            "emvr_formula_phase": EXPERIMENT_DIRECTION_REVIEW,
                            "experiment_brief_draft": deepcopy(brief),
                            "pending_action": {
                                "type": "CONFIRM_EMVR_FORMULA_DIRECTION",
                                "subject": "experiment_brief",
                                "proposal": deepcopy(brief),
                                "advance_on_accept": True,
                                "question": "这版方向是否可以锁定；如仍需修改，请直接指出具体部分。",
                            },
                        },
                        student_task="这版方向是否可以锁定；如仍需修改，请直接指出具体部分。",
                    ),
                    False,
                )
        should_lock = bool(
            lock_action is not None
            or complete_stage
            or controls.intersection({"ACCEPT", "ADVANCE"})
        )
        if not should_lock:
            return (
                StepOutput(
                    assistant_message=(
                        "实验方向草稿仍保持当前版本。你可以一次修改多个部分；我会分别更新对象、操作、变化量、观察量和边界条件。"
                    ),
                    stage_payload={
                        "emvr_formula_phase": EXPERIMENT_DIRECTION_REVIEW,
                        "experiment_brief_draft": deepcopy(flow.get("experiment_brief")),
                        "preserve_pending_action": True,
                    },
                    student_task="请说明需要调整的部分，或确认按这份方向继续。",
                ),
                False,
            )
        if lock_action and isinstance(lock_action.get("brief_updates"), dict):
            brief = flow.get("experiment_brief", {})
            _apply_brief_updates(brief, lock_action.get("brief_updates"))
        _clear_semantic_recovery(flow)
        _commit_brief(session, flow)
        flow["phase"] = EXPERIMENT_BRIEF_COMPLETED
        flow["direction_locked"] = True
        flow["phase"] = EMVR_DETAIL_DESIGN
        brief = flow["experiment_brief"]
        return (
            StepOutput(
                assistant_message=(
                    "实验方向已经锁定。主要公式、辅助公式、研究对象、操作、变化量、观察量和适用边界已经形成同一份设计起点，"
                    "后续只会在这个方向内完善 Unity VR 细节，不会重新替换理论主线。"
                ),
                stage_payload={
                    "emvr_formula_phase": EMVR_DETAIL_DESIGN,
                    "experiment_brief": deepcopy(brief),
                    "formula_selection": deepcopy(flow["formula_selection"]),
                    "direction_locked": True,
                },
                student_task=None,
            ),
            True,
        )

    return (
        StepOutput(
            assistant_message="实验方向已经锁定，我们继续完善后续 EMVR 设计。",
            stage_payload={"emvr_formula_phase": EMVR_DETAIL_DESIGN},
        ),
        True,
    )
