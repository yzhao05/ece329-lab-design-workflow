from __future__ import annotations

import hashlib
import json
import random
import re
from importlib.resources import files
from typing import Any


class LectureKnowledgeBase:
    def __init__(self) -> None:
        root = files("ece329_workflow").joinpath("knowledge")
        self.manifest = json.loads(root.joinpath("source_manifest.json").read_text(encoding="utf-8"))
        self.concept_data = json.loads(root.joinpath("concepts.json").read_text(encoding="utf-8"))
        self.formula_data = json.loads(root.joinpath("formulas.json").read_text(encoding="utf-8"))
        self.formula_design_data = json.loads(
            root.joinpath("formula_design_profiles.json").read_text(encoding="utf-8")
        )
        self.scene_formula_data = json.loads(
            root.joinpath("scene_formula_links.json").read_text(encoding="utf-8")
        )
        self.experiment_pattern_data = json.loads(
            root.joinpath("experiment_design_patterns.json").read_text(encoding="utf-8")
        )
        self.supplemental_data = json.loads(
            root.joinpath("supplemental_sources.json").read_text(encoding="utf-8")
        )
        self.scene_template_data = json.loads(
            root.joinpath("scene_templates.json").read_text(encoding="utf-8")
        )
        self.lectures: list[dict[str, Any]] = self.concept_data["lectures"]
        self.baseline_comparisons: list[dict[str, Any]] = self.concept_data.get(
            "baseline_comparisons",
            [],
        )
        self.formulas: list[dict[str, Any]] = self.formula_data["formulas"]
        self.formula_design_profiles: list[dict[str, Any]] = self.formula_design_data[
            "profiles"
        ]
        self.profile_scene_links: list[dict[str, Any]] = self.scene_formula_data[
            "profile_scene_links"
        ]
        self.scene_formula_roles: dict[str, dict[str, Any]] = (
            self.scene_formula_data["scene_formula_roles"]
        )
        self.experiment_design_patterns: list[dict[str, Any]] = (
            self.experiment_pattern_data["patterns"]
        )
        self.formula_profile_pattern_links: list[dict[str, Any]] = (
            self.experiment_pattern_data["profile_applicability"]
        )
        self.supplemental_sources: list[dict[str, Any]] = self.supplemental_data["sources"]
        self.supplemental_concepts: list[dict[str, Any]] = self.supplemental_data["concepts"]
        self.scene_templates: list[dict[str, Any]] = self.scene_template_data["templates"]
        self.generic_scene_frames: list[dict[str, Any]] = self.scene_template_data[
            "generic_frames"
        ]
        self._lecture_by_id = {item["id"]: item for item in self.lectures}
        self._formula_by_id = {item["id"]: item for item in self.formulas}
        self._formula_profile_by_id = {
            item["profile_id"]: item for item in self.formula_design_profiles
        }
        self._experiment_pattern_by_id = {
            item["pattern_id"]: item for item in self.experiment_design_patterns
        }
        self._pattern_ids_by_formula_profile = {
            str(item.get("profile_id") or ""): [
                str(pattern_id)
                for pattern_id in item.get("pattern_ids", [])
                if str(pattern_id).strip()
            ]
            for item in self.formula_profile_pattern_links
        }
        self._supplemental_source_by_id = {
            item["source_id"]: item for item in self.supplemental_sources
        }
        self._supplemental_concept_by_id = {
            item["supplemental_concept_id"]: item for item in self.supplemental_concepts
        }
        self.exploration_points = self._build_exploration_points()
        self._exploration_by_scene_id = {
            item["catalog_scene_id"]: item for item in self.exploration_points
        }
        self._formula_profile_ids_by_scene_id = self._build_scene_formula_index()

    @property
    def source_reference(self) -> dict[str, Any]:
        return {
            "source_id": self.manifest["source_id"],
            "title": self.manifest["title"],
            "sha256": self.manifest["sha256"],
            "page_count": self.manifest["page_count"],
        }

    @property
    def source_references(self) -> list[dict[str, Any]]:
        return [self.source_reference, *[dict(source) for source in self.supplemental_sources]]

    def match_concepts(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        normalized = text.casefold()
        scored: list[tuple[int, dict[str, Any]]] = []
        for lecture in self.lectures:
            score = 0
            for keyword in lecture["keywords"]:
                keyword_normalized = keyword.casefold()
                if self._term_matches(keyword_normalized, normalized):
                    score += max(2, len(keyword_normalized.split()))
            for concept in lecture["concepts"]:
                if self._term_matches(concept.casefold(), normalized):
                    score += 3
            if score:
                scored.append((score, lecture))
        scored.sort(key=lambda item: (-item[0], item[1]["lecture"]))
        return [item for _, item in scored[:limit]]

    def broad_entry_points(self) -> list[dict[str, Any]]:
        overview = self.concept_data["overview"]
        descriptions = {
            "electrostatics": "静电场与电势、介质、极化或电容结构之间的关系",
            "magnetism": "磁场与电流、磁通变化、电磁感应或电感之间的关系",
            "electromagnetics": "电磁波与偏振、界面反射、导体衰减或传输线之间的关系",
        }
        labels = {
            "electrostatics": "静电场与材料、边界",
            "magnetism": "磁场与电磁感应",
            "electromagnetics": "电磁波与传输线",
        }
        return [
            {
                "option_id": f"course_block:{block['id']}",
                "direction": labels[block["id"]],
                "focus": descriptions[block["id"]],
                "source_pages": overview["pages"],
                "source_lectures": block["lectures"],
                "concept_id": block["id"],
            }
            for block in overview["course_blocks"]
        ]

    @staticmethod
    def _term_matches(term: str, text: str) -> bool:
        """Match Latin catalog terms by token boundary and CJK terms by substring."""

        if not term:
            return False
        if re.search(r"[a-z0-9]", term) and not re.search(r"[\u3400-\u9fff]", term):
            return re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                text,
            ) is not None
        counted_entity = re.fullmatch(r"(两个|两)([\u3400-\u9fff]{1,4})", term)
        if counted_entity:
            quantity, entity = counted_entity.groups()
            if re.search(
                rf"{re.escape(quantity)}[\u3400-\u9fff]{{0,12}}{re.escape(entity)}",
                text,
            ):
                return True
        return term in text

    def match_supplemental_concepts(
        self,
        text: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        normalized = text.casefold()
        scored: list[tuple[int, dict[str, Any]]] = []
        for concept in self.supplemental_concepts:
            score = 0
            for keyword in concept["keywords"]:
                keyword_normalized = keyword.casefold()
                if self._term_matches(keyword_normalized, normalized):
                    score += max(2, len(keyword_normalized.split()))
            for label in concept["concepts"]:
                if self._term_matches(label.casefold(), normalized):
                    score += 3
            if score:
                scored.append((score, concept))
        scored.sort(key=lambda item: (-item[0], item[1]["supplemental_concept_id"]))
        return [item for _, item in scored[:limit]]

    def supplemental_concept_references(
        self,
        text: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in self.match_supplemental_concepts(text, limit)]

    @staticmethod
    def _course_block_for_lecture(lecture: int) -> str:
        if lecture <= 11:
            return "electrostatics"
        if lecture <= 15:
            return "magnetism"
        return "electromagnetics"

    def _course_block_for_scope(self, concept_ids: list[str]) -> str:
        counts = {"electrostatics": 0, "magnetism": 0, "electromagnetics": 0}
        for concept_id in concept_ids:
            lecture = self._lecture_by_id.get(concept_id)
            if lecture:
                counts[self._course_block_for_lecture(int(lecture["lecture"]))] += 1
        return max(counts, key=lambda key: (counts[key], -list(counts).index(key)))

    def _build_exploration_points(self) -> list[dict[str, Any]]:
        """Expand every cataloged lecture axis and supplemental relation into one point."""

        points: list[dict[str, Any]] = []

        def add_point(point: dict[str, Any]) -> None:
            number = len(points) + 1
            points.append(
                {
                    "catalog_scene_id": f"ECE329-S{number:03d}",
                    "catalog_scene_number": number,
                    **point,
                }
            )

        for lecture in self.lectures:
            for axis_index, axis in enumerate(lecture["brainstorm_axes"], start=1):
                add_point(
                    {
                        "option_id": f"lecture:{lecture['id']}:{axis_index}",
                        "direction": lecture["title"],
                        "focus": axis,
                        "concept_id": lecture["id"],
                        "source_lecture": lecture["lecture"],
                        "source_pages": list(lecture["pages"]),
                        "course_block": self._course_block_for_lecture(
                            int(lecture["lecture"])
                        ),
                        "catalog_keywords": list(lecture["keywords"]),
                        "catalog_source_type": "LECTURE_AXIS",
                    }
                )

        for concept in self.supplemental_concepts:
            concept_id = str(concept["supplemental_concept_id"])
            scope_ids = list(concept["course_scope_concept_ids"])
            for relation_index, relation in enumerate(
                concept["relationship_examples"],
                start=1,
            ):
                add_point(
                    {
                        "option_id": f"supplemental:{concept_id}:{relation_index}",
                        "direction": relation["direction"],
                        "focus": relation["focus"],
                        "references": [
                            {
                                **dict(reference),
                                "source_title": self._supplemental_source_by_id[
                                    reference["source_id"]
                                ]["title"],
                            }
                            for reference in relation["references"]
                        ],
                        "supplemental_concept_id": concept_id,
                        "course_scope_concept_ids": scope_ids,
                        "course_block": self._course_block_for_scope(scope_ids),
                        "catalog_keywords": list(concept["keywords"]),
                        "catalog_source_type": "SUPPLEMENTAL_RELATION",
                    }
                )
        return points

    def exploration_scene_catalog(self) -> list[dict[str, Any]]:
        return [dict(point) for point in self.exploration_points]

    def _build_scene_formula_index(self) -> dict[str, list[str]]:
        """Build the many-to-many index without changing exploration scenes."""

        index: dict[str, list[str]] = {}
        for link in self.profile_scene_links:
            profile_id = str(link.get("profile_id") or "")
            for raw_scene_id in link.get("scene_ids", []):
                scene_id = str(raw_scene_id)
                profile_ids = index.setdefault(scene_id, [])
                if profile_id and profile_id not in profile_ids:
                    profile_ids.append(profile_id)
        return index

    def formula_links_for_scene(self, scene_id: str) -> dict[str, Any] | None:
        """Return canonical formula roles for one internal exploration scene."""

        normalized_scene_id = str(scene_id).strip()
        scene = self._exploration_by_scene_id.get(normalized_scene_id)
        if not scene:
            return None
        profile_ids = self._formula_profile_ids_by_scene_id.get(
            normalized_scene_id,
            [],
        )
        profiles = [
            self._materialize_formula_design_profile(
                self._formula_profile_by_id[profile_id]
            )
            for profile_id in profile_ids
            if profile_id in self._formula_profile_by_id
        ]
        role_definition = self.scene_formula_roles.get(normalized_scene_id, {})
        primary_ids = list(
            dict.fromkeys(
                str(formula_id)
                for formula_id in role_definition.get("primary_formula_ids", [])
            )
        )
        supporting_ids = [
            formula_id
            for formula_id in dict.fromkeys(
                str(formula_id)
                for formula_id in role_definition.get(
                    "supporting_formula_ids", []
                )
            )
            if formula_id not in primary_ids
        ]
        return {
            "scene_id": normalized_scene_id,
            "option_id": scene.get("option_id"),
            "direction": scene.get("direction"),
            "focus": scene.get("focus"),
            "profile_ids": list(profile_ids),
            "primary_formula_ids": primary_ids,
            "supporting_formula_ids": supporting_ids,
            "primary_formulas": [
                dict(self._formula_by_id[formula_id])
                for formula_id in primary_ids
                if formula_id in self._formula_by_id
            ],
            "supporting_formulas": [
                dict(self._formula_by_id[formula_id])
                for formula_id in supporting_ids
                if formula_id in self._formula_by_id
            ],
            "formula_design_profiles": profiles,
        }

    def formula_links_for_scenes(
        self,
        scene_ids: list[str] | set[str] | tuple[str, ...],
    ) -> list[dict[str, Any]]:
        return [
            link
            for scene_id in dict.fromkeys(str(item) for item in scene_ids)
            if (link := self.formula_links_for_scene(scene_id)) is not None
        ]

    def scenes_for_formula(self, formula_id: str) -> list[dict[str, Any]]:
        """Return every linked scene, supporting the formula-to-many direction."""

        normalized_formula_id = str(formula_id).strip()
        if normalized_formula_id not in self._formula_by_id:
            return []
        scenes: list[dict[str, Any]] = []
        for scene_id, scene in self._exploration_by_scene_id.items():
            link = self.formula_links_for_scene(scene_id)
            if not link:
                continue
            primary_ids = link.get("primary_formula_ids", [])
            supporting_ids = link.get("supporting_formula_ids", [])
            if normalized_formula_id not in {*primary_ids, *supporting_ids}:
                continue
            scenes.append(
                {
                    "scene_id": scene_id,
                    "option_id": scene.get("option_id"),
                    "direction": scene.get("direction"),
                    "focus": scene.get("focus"),
                    "formula_role": (
                        "PRIMARY"
                        if normalized_formula_id in primary_ids
                        else "SUPPORTING"
                    ),
                }
            )
        return scenes

    def _scene_matches_formula_domain(
        self,
        point: dict[str, Any],
        course_domain: str,
    ) -> bool:
        """Require a scene's formula binding to agree with its topic block."""

        normalized_domain = str(course_domain or "").strip().casefold()
        if not normalized_domain:
            return True
        if str(point.get("course_block") or "") != normalized_domain:
            return False
        scene_id = str(point.get("catalog_scene_id") or "")
        profile_ids = self._formula_profile_ids_by_scene_id.get(scene_id, [])
        if not profile_ids:
            return True
        return any(
            str(
                self._formula_profile_by_id.get(profile_id, {}).get("course_block")
                or ""
            )
            == normalized_domain
            for profile_id in profile_ids
        )

    def _relevant_exploration_points(
        self,
        text: str,
        *,
        course_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_domain = str(course_domain or "").strip().casefold()
        if normalized_domain not in {
            "electrostatics",
            "magnetism",
            "electromagnetics",
        }:
            normalized_domain = ""

        lecture_matches = self.match_concepts(text, limit=len(self.lectures))
        supplemental_matches = self.match_supplemental_concepts(
            text,
            limit=len(self.supplemental_concepts),
        )
        if not lecture_matches and not supplemental_matches:
            return [
                point
                for point in self.exploration_points
                if not normalized_domain
                or self._scene_matches_formula_domain(point, normalized_domain)
            ]

        lecture_ids = {str(item["id"]) for item in lecture_matches}
        supplemental_ids = {
            str(item["supplemental_concept_id"]) for item in supplemental_matches
        }
        for concept in supplemental_matches:
            lecture_ids.update(str(item) for item in concept["course_scope_concept_ids"])
        matched = [
            point
            for point in self.exploration_points
            if str(point.get("concept_id") or "") in lecture_ids
            or str(point.get("supplemental_concept_id") or "") in supplemental_ids
        ]
        if normalized_domain:
            matched = [
                point
                for point in matched
                if self._scene_matches_formula_domain(point, normalized_domain)
            ]
            if not matched:
                matched = [
                    point
                    for point in self.exploration_points
                    if self._scene_matches_formula_domain(point, normalized_domain)
                ]
        return matched

    @staticmethod
    def _sample_seed(seed_key: str, excluded_count: int) -> int:
        digest = hashlib.sha256(
            f"{seed_key}|{excluded_count}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big")

    def brainstorm_options(
        self,
        text: str,
        limit: int = 3,
        *,
        exclude_option_ids: set[str] | None = None,
        seed_key: str = "",
        course_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Sample unique, course-grounded points while avoiding previously shown ones."""

        if limit <= 0:
            return []
        excluded = {str(item) for item in (exclude_option_ids or set()) if str(item)}
        normalized_domain = str(course_domain or "").strip().casefold()
        if normalized_domain not in {
            "electrostatics",
            "magnetism",
            "electromagnetics",
        }:
            normalized_domain = ""
        relevant = self._relevant_exploration_points(
            text,
            course_domain=normalized_domain or None,
        )
        remaining = [
            point for point in relevant if str(point["option_id"]) not in excluded
        ]
        if len(remaining) < limit:
            known_ids = {str(point["option_id"]) for point in remaining}
            remaining.extend(
                point
                for point in self.exploration_points
                if str(point["option_id"]) not in excluded
                and str(point["option_id"]) not in known_ids
                and (
                    not normalized_domain
                    or self._scene_matches_formula_domain(point, normalized_domain)
                )
            )
        if len(remaining) < limit:
            remaining = list(relevant or self.exploration_points)

        rng = random.Random(self._sample_seed(seed_key, len(excluded)))
        no_specific_match = (
            not normalized_domain
            and len(relevant) == len(self.exploration_points)
        )
        if no_specific_match and limit == 3:
            sampled: list[dict[str, Any]] = []
            for block in ("electrostatics", "magnetism", "electromagnetics"):
                block_points = [
                    point for point in remaining if point.get("course_block") == block
                ]
                if block_points:
                    sampled.append(rng.choice(block_points))
            if len(sampled) == 3:
                rng.shuffle(sampled)
                return [dict(point) for point in sampled]

        count = min(limit, len(remaining))
        return [dict(point) for point in rng.sample(remaining, count)]

    def standard_comparison_suggestions(
        self,
        text: str,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Return course-cataloged basic case bundles whose trigger groups match."""

        normalized = text.casefold()
        scored: list[tuple[int, dict[str, Any]]] = []
        for comparison in self.baseline_comparisons:
            trigger_groups = comparison.get("trigger_groups", [])
            if not isinstance(trigger_groups, list) or not trigger_groups:
                continue
            matched_terms: list[str] = []
            matched = True
            for group in trigger_groups:
                terms = group if isinstance(group, list) else []
                group_matches = [
                    str(term)
                    for term in terms
                    if self._term_matches(str(term).casefold(), normalized)
                ]
                if not group_matches:
                    matched = False
                    break
                matched_terms.extend(group_matches)
            if matched:
                score = sum(max(1, len(term)) for term in matched_terms)
                scored.append((score, comparison))
        scored.sort(key=lambda item: (-item[0], item[1]["comparison_id"]))
        return [
            {
                "comparison_id": item["comparison_id"],
                "cases": list(item["cases"]),
                "recommended_cases": list(item["cases"]),
                "case_aliases": {
                    str(case): list(aliases)
                    for case, aliases in item.get("case_aliases", {}).items()
                },
                "role": "PROPOSED_BASELINE_COMPARISON",
                "adoption_status": "PENDING",
                "reason": item["reason"],
                "course_concept_ids": list(item["course_concept_ids"]),
                "proposal_source": "COURSE_CATALOG",
            }
            for _, item in scored[:limit]
        ]

    def scene_components(
        self,
        direction: str,
        index: int,
        *,
        excluded_signatures: set[str] | None = None,
    ) -> tuple[str, str, str, str]:
        """Select a course-grounded scene by catalog terms, with a generic fallback.

        Scene routing lives in data so adding another ECE329 concept does not require a
        new Python branch.  The score favors more and longer matching catalog terms.
        """

        template = self.scene_template(
            direction,
            index,
            excluded_signatures=excluded_signatures,
        )
        return (
            str(template["title"]),
            str(template["physical_picture"]),
            str(template["thinking_prompt"]),
            str(template["illustrative_extension"]),
        )

    def scene_template(
        self,
        direction: str,
        index: int,
        *,
        excluded_signatures: set[str] | None = None,
    ) -> dict[str, Any]:
        """Return a scene together with its stable cross-turn identity."""

        normalized = direction.casefold()
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for order, template in enumerate(self.scene_templates):
            matches = [
                str(keyword)
                for keyword in template.get("keywords", [])
                if self._term_matches(str(keyword).casefold(), normalized)
            ]
            if matches:
                score = sum(max(1, len(keyword)) for keyword in matches)
                candidates.append((score, -order, template))
        excluded = excluded_signatures or set()
        ranked_templates = [
            item[2]
            for item in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)
        ]
        generic_templates = [
            self.generic_scene_frames[(index + offset) % len(self.generic_scene_frames)]
            for offset in range(len(self.generic_scene_frames))
        ]
        ranked_ids = {id(item) for item in ranked_templates}
        remaining_course_templates = [
            item for item in self.scene_templates if id(item) not in ranked_ids
        ]
        # A second request for examples must not fall back to a frame the
        # student has already seen merely because one concept has only one
        # direct template. Generic frames come next; if those are exhausted,
        # another verified ECE329 scene is preferable to a visible duplicate.
        choices = [
            *ranked_templates,
            *generic_templates,
            *remaining_course_templates,
        ]
        selected = next(
            (
                item
                for item in choices
                if self._scene_signature(item) not in excluded
            ),
            choices[0],
        )
        signature = self._scene_signature(selected)
        template_id = str(selected.get("template_id") or "").strip()
        if not template_id:
            template_id = "generic_" + hashlib.sha256(
                signature.encode("utf-8")
            ).hexdigest()[:16]
        return {
            **selected,
            "template_id": template_id,
            "template_signature": signature,
        }

    @staticmethod
    def _scene_signature(template: dict[str, Any]) -> str:
        """Identify the visible physical frame, independent of course anchor."""

        return "|".join(
            " ".join(str(template.get(field) or "").split()).casefold()
            for field in ("title", "physical_picture", "thinking_prompt")
        )

    def scene_signature(self, template: dict[str, Any]) -> str:
        """Public stable identity for a visible physical scene template."""

        return self._scene_signature(template)

    def concept_references(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        matches = self.match_concepts(text, limit=limit)
        if not matches:
            scope_ids: list[str] = []
            for supplemental in self.match_supplemental_concepts(text, limit=1):
                scope_ids.extend(supplemental["course_scope_concept_ids"])
            matches = [
                self._lecture_by_id[concept_id]
                for concept_id in dict.fromkeys(scope_ids)
                if concept_id in self._lecture_by_id
            ][:limit]
        return [
            {
                "concept_id": item["id"],
                "lecture": item["lecture"],
                "title": item["title"],
                "pages": item["pages"],
                "concepts": item["concepts"],
            }
            for item in matches
        ]

    def formula_references(self, text: str, limit: int = 8) -> list[dict[str, Any]]:
        concept_ids = {item["id"] for item in self.match_concepts(text, limit=5)}
        if not concept_ids:
            for supplemental in self.match_supplemental_concepts(text, limit=1):
                concept_ids.update(supplemental["course_scope_concept_ids"])
        if not concept_ids:
            return []
        matches = [
            formula
            for formula in self.formulas
            if concept_ids.intersection(formula["concept_ids"])
        ]
        return matches[:limit]

    def focused_formula_references(
        self,
        text: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return formulas tied to the strongest course concept match.

        ``formula_references`` intentionally exposes a broad retrieval set for
        evidence panels.  A design summary needs a narrower contract: formulas
        from adjacent matched lectures must not be presented as if they all
        explain the student's experiment.  Selecting the first concept that
        actually has formulas keeps this decision grounded in the existing
        semantic course matcher rather than in a new vocabulary of topics.
        """

        concept_ids = [
            str(item.get("id") or "")
            for item in self.match_concepts(text, limit=5)
            if str(item.get("id") or "")
        ]
        if not concept_ids:
            for supplemental in self.match_supplemental_concepts(text, limit=1):
                concept_ids.extend(
                    str(item)
                    for item in supplemental.get("course_scope_concept_ids", [])
                    if str(item).strip()
                )
        for concept_id in dict.fromkeys(concept_ids):
            matches = [
                formula
                for formula in self.formulas
                if concept_id in formula.get("concept_ids", [])
            ]
            if matches:
                return matches[:limit]
        return []

    def _materialize_formula_design_profile(
        self,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve stable IDs to canonical formula records with lecture provenance."""

        primary_ids = [str(item) for item in profile.get("primary_formula_ids", [])]
        supporting_ids = [
            str(item) for item in profile.get("supporting_formula_ids", [])
        ]
        return {
            **dict(profile),
            "applicable_experiment_pattern_ids": list(
                self._pattern_ids_by_formula_profile.get(
                    str(profile.get("profile_id") or ""),
                    [],
                )
            ),
            "primary_formulas": [
                dict(self._formula_by_id[formula_id])
                for formula_id in primary_ids
                if formula_id in self._formula_by_id
            ],
            "supporting_formulas": [
                dict(self._formula_by_id[formula_id])
                for formula_id in supporting_ids
                if formula_id in self._formula_by_id
            ],
        }

    def design_profiles_for_formula_ids(
        self,
        formula_ids: list[str] | set[str] | tuple[str, ...],
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Return design profiles connected to an already selected formula set."""

        selected = {str(item) for item in formula_ids if str(item).strip()}
        if not selected or limit <= 0:
            return []
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for order, profile in enumerate(self.formula_design_profiles):
            primary_ids = set(profile.get("primary_formula_ids", []))
            supporting_ids = set(profile.get("supporting_formula_ids", []))
            primary_matches = len(selected.intersection(primary_ids))
            supporting_matches = len(selected.intersection(supporting_ids))
            if primary_matches or supporting_matches:
                ranked.append(
                    (primary_matches * 10 + supporting_matches, -order, profile)
                )
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return [
            self._materialize_formula_design_profile(profile)
            for _, _, profile in ranked[:limit]
        ]

    def formula_design_references(
        self,
        text: str,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Retrieve formula-to-design mappings through course concept links.

        The lookup reuses the established lecture concept matcher. It does not
        infer design roles from isolated keywords or persist any formula choice.
        """

        if limit <= 0:
            return []
        ranked_concept_ids = [
            str(item.get("id") or "")
            for item in self.match_concepts(text, limit=8)
            if str(item.get("id") or "")
        ]
        if not ranked_concept_ids:
            for supplemental in self.match_supplemental_concepts(text, limit=2):
                ranked_concept_ids.extend(
                    str(item)
                    for item in supplemental.get("course_scope_concept_ids", [])
                    if str(item).strip()
                )
        if not ranked_concept_ids:
            return []

        concept_rank = {
            concept_id: index
            for index, concept_id in enumerate(dict.fromkeys(ranked_concept_ids))
        }
        block_rank = {
            self._course_block_for_lecture(
                int(self._lecture_by_id[concept_id]["lecture"])
            ): index
            for index, concept_id in enumerate(dict.fromkeys(ranked_concept_ids))
            if concept_id in self._lecture_by_id
        }
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for order, profile in enumerate(self.formula_design_profiles):
            primary_ids = profile.get("primary_formula_ids", [])
            supporting_ids = profile.get("supporting_formula_ids", [])
            primary_concepts = {
                str(concept_id)
                for formula_id in primary_ids
                for concept_id in self._formula_by_id.get(formula_id, {}).get(
                    "concept_ids", []
                )
            }
            supporting_concepts = {
                str(concept_id)
                for formula_id in supporting_ids
                for concept_id in self._formula_by_id.get(formula_id, {}).get(
                    "concept_ids", []
                )
            }
            matched_primary = primary_concepts.intersection(concept_rank)
            matched_supporting = supporting_concepts.intersection(concept_rank)
            if not matched_primary and not matched_supporting:
                continue
            best_rank = min(
                concept_rank[item]
                for item in matched_primary.union(matched_supporting)
            )
            score = (
                len(matched_primary) * 20
                + len(matched_supporting) * 4
                + max(0, 10 - best_rank)
            )
            profile_block = str(profile.get("course_block") or "")
            if profile_block in block_rank:
                score += max(20, 50 - block_rank[profile_block] * 5)
            ranked.append((score, -order, profile))
        matched_profile_ids = {str(item[2].get("profile_id") or "") for item in ranked}
        # A broad course concept (for example "静电场") may match only an
        # overview lecture. Fill the candidate set from the same established
        # course block so the knowledge layer exposes usable formula families
        # instead of unrelated equations from a neighboring topic.
        for order, profile in enumerate(self.formula_design_profiles):
            profile_id = str(profile.get("profile_id") or "")
            profile_block = str(profile.get("course_block") or "")
            if profile_id in matched_profile_ids or profile_block not in block_rank:
                continue
            ranked.append((max(10, 40 - block_rank[profile_block] * 5), -order, profile))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return [
            self._materialize_formula_design_profile(profile)
            for _, _, profile in ranked[:limit]
        ]

    def public_concepts(self) -> list[dict[str, Any]]:
        return self.lectures

    def public_supplemental_concepts(self) -> list[dict[str, Any]]:
        return self.supplemental_concepts

    def public_formulas(self) -> list[dict[str, Any]]:
        return self.formulas

    def public_formula_design_profiles(self) -> list[dict[str, Any]]:
        return [
            self._materialize_formula_design_profile(profile)
            for profile in self.formula_design_profiles
        ]

    def public_experiment_design_patterns(self) -> list[dict[str, Any]]:
        return [dict(pattern) for pattern in self.experiment_design_patterns]

    def experiment_patterns_for_profiles(
        self,
        profile_ids: list[str] | set[str] | tuple[str, ...],
    ) -> list[dict[str, Any]]:
        selected = {str(item) for item in profile_ids if str(item).strip()}
        applicable = {
            pattern_id
            for profile_id in selected
            for pattern_id in self._pattern_ids_by_formula_profile.get(profile_id, [])
        }
        return [
            dict(pattern)
            for pattern in self.experiment_design_patterns
            if pattern.get("pattern_id") in applicable
        ]

    def public_scene_formula_links(self) -> list[dict[str, Any]]:
        return self.formula_links_for_scenes(
            [item["catalog_scene_id"] for item in self.exploration_points]
        )

    def search(self, text: str) -> dict[str, Any]:
        concepts = self.concept_references(text, limit=5)
        supplemental = self.supplemental_concept_references(text, limit=5)
        brainstorm_options = self.brainstorm_options(text, limit=3)
        formula_profiles = self.formula_design_references(text, limit=6)
        relevant_pattern_ids = {
            str(pattern_id)
            for profile in formula_profiles
            for pattern_id in profile.get("applicable_experiment_pattern_ids", [])
        }
        return {
            "query": text,
            "source": self.source_reference,
            "course_scope_source": self.source_reference,
            "sources": self.source_references,
            "concepts": concepts,
            "supplemental_concepts": supplemental,
            "formulas": self.formula_references(text, limit=12),
            "formula_design_profiles": formula_profiles,
            "experiment_design_patterns": [
                dict(pattern)
                for pattern in self.experiment_design_patterns
                if pattern.get("pattern_id") in relevant_pattern_ids
            ],
            "brainstorm_options": brainstorm_options,
            "scene_formula_links": self.formula_links_for_scenes(
                [
                    str(item.get("catalog_scene_id") or "")
                    for item in brainstorm_options
                    if str(item.get("catalog_scene_id") or "")
                ]
            ),
            "baseline_comparison_suggestions": self.standard_comparison_suggestions(
                text,
                limit=1,
            ),
            "fallback_used": not bool(concepts or supplemental),
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        valid_concept_ids = set(self._lecture_by_id)
        formula_ids: set[str] = set()
        for formula in self.formulas:
            formula_id = formula["id"]
            if formula_id in formula_ids:
                errors.append(f"duplicate formula id: {formula_id}")
            formula_ids.add(formula_id)
            missing = set(formula["concept_ids"]) - valid_concept_ids
            if missing:
                errors.append(f"formula {formula_id} has unknown concept ids: {sorted(missing)}")
            for page in formula["pages"]:
                if not 1 <= page <= self.manifest["page_count"]:
                    errors.append(f"formula {formula_id} has invalid page: {page}")
        if self.formula_design_data.get("source_id") != self.manifest.get("source_id"):
            errors.append("formula design profile source does not match the lecture manifest")
        profile_ids: set[str] = set()
        covered_formula_ids: set[str] = set()
        valid_course_blocks = {"electrostatics", "magnetism", "electromagnetics"}
        for profile in self.formula_design_profiles:
            profile_id = profile.get("profile_id")
            if not isinstance(profile_id, str) or not profile_id:
                errors.append("formula design profile has an invalid id")
                continue
            if profile_id in profile_ids:
                errors.append(f"duplicate formula design profile id: {profile_id}")
            profile_ids.add(profile_id)
            if profile.get("course_block") not in valid_course_blocks:
                errors.append(f"formula design profile {profile_id} has invalid course block")
            primary_ids = profile.get("primary_formula_ids")
            supporting_ids = profile.get("supporting_formula_ids")
            if not isinstance(primary_ids, list) or not primary_ids:
                errors.append(f"formula design profile {profile_id} has no primary formulas")
                primary_ids = []
            if not isinstance(supporting_ids, list):
                errors.append(f"formula design profile {profile_id} has invalid supporting formulas")
                supporting_ids = []
            referenced_ids = {str(item) for item in [*primary_ids, *supporting_ids]}
            unknown_formula_ids = referenced_ids - formula_ids
            if unknown_formula_ids:
                errors.append(
                    f"formula design profile {profile_id} has unknown formula ids: "
                    f"{sorted(unknown_formula_ids)}"
                )
            duplicated_roles = set(primary_ids).intersection(supporting_ids)
            if duplicated_roles:
                errors.append(
                    f"formula design profile {profile_id} assigns formulas to both roles: "
                    f"{sorted(duplicated_roles)}"
                )
            covered_formula_ids.update(referenced_ids.intersection(formula_ids))
            for field in (
                "supported_variations",
                "supported_observations",
                "boundary_conditions",
            ):
                values = profile.get(field)
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(item, dict) or not item for item in values)
                ):
                    errors.append(
                        f"formula design profile {profile_id} has invalid {field}"
                    )
        pattern_ids: set[str] = set()
        for pattern in self.experiment_design_patterns:
            pattern_id = str(pattern.get("pattern_id") or "")
            if not pattern_id:
                errors.append("experiment design pattern has an invalid id")
                continue
            if pattern_id in pattern_ids:
                errors.append(f"duplicate experiment design pattern id: {pattern_id}")
            pattern_ids.add(pattern_id)
            for field in ("title_zh", "design_logic", "required_capabilities", "scene_requirements", "method_template"):
                value = pattern.get(field)
                if value in (None, "", [], {}):
                    errors.append(f"experiment design pattern {pattern_id} has invalid {field}")
        linked_pattern_profiles: set[str] = set()
        for link in self.formula_profile_pattern_links:
            profile_id = str(link.get("profile_id") or "")
            if profile_id not in profile_ids:
                errors.append(f"experiment pattern link has unknown formula profile: {profile_id}")
            if profile_id in linked_pattern_profiles:
                errors.append(f"duplicate experiment pattern profile link: {profile_id}")
            linked_pattern_profiles.add(profile_id)
            linked_ids = link.get("pattern_ids")
            if not isinstance(linked_ids, list) or not linked_ids:
                errors.append(f"formula design profile {profile_id} has no experiment patterns")
                continue
            unknown_patterns = {str(item) for item in linked_ids} - pattern_ids
            if unknown_patterns:
                errors.append(
                    f"formula design profile {profile_id} has unknown experiment patterns: "
                    f"{sorted(unknown_patterns)}"
                )
        missing_pattern_profiles = profile_ids - linked_pattern_profiles
        if missing_pattern_profiles:
            errors.append(
                "formula design profiles do not declare experiment patterns: "
                f"{sorted(missing_pattern_profiles)}"
            )
        missing_profile_formulas = formula_ids - covered_formula_ids
        if missing_profile_formulas:
            errors.append(
                "formula design profiles do not cover formulas: "
                f"{sorted(missing_profile_formulas)}"
            )
        valid_scene_ids = set(self._exploration_by_scene_id)
        linked_scene_ids: set[str] = set()
        linked_profile_ids: set[str] = set()
        for link in self.profile_scene_links:
            profile_id = str(link.get("profile_id") or "")
            if not profile_id or profile_id not in profile_ids:
                errors.append(
                    f"scene-formula link has unknown formula profile: {profile_id}"
                )
            if profile_id in linked_profile_ids:
                errors.append(
                    f"duplicate scene-formula profile link: {profile_id}"
                )
            linked_profile_ids.add(profile_id)
            scene_ids = link.get("scene_ids")
            if not isinstance(scene_ids, list) or not scene_ids:
                errors.append(
                    f"scene-formula profile {profile_id} has no scene ids"
                )
                continue
            normalized_scene_ids = [str(item) for item in scene_ids]
            if len(normalized_scene_ids) != len(set(normalized_scene_ids)):
                errors.append(
                    f"scene-formula profile {profile_id} repeats a scene id"
                )
            unknown_scene_ids = set(normalized_scene_ids) - valid_scene_ids
            if unknown_scene_ids:
                errors.append(
                    f"scene-formula profile {profile_id} has unknown scene ids: "
                    f"{sorted(unknown_scene_ids)}"
                )
            linked_scene_ids.update(set(normalized_scene_ids).intersection(valid_scene_ids))
        missing_scene_links = valid_scene_ids - linked_scene_ids
        if missing_scene_links:
            errors.append(
                "exploration scenes without formula links: "
                f"{sorted(missing_scene_links)}"
            )
        missing_profile_links = profile_ids - linked_profile_ids
        if missing_profile_links:
            errors.append(
                "formula design profiles without scene links: "
                f"{sorted(missing_profile_links)}"
            )
        role_scene_ids = set(self.scene_formula_roles)
        missing_scene_roles = valid_scene_ids - role_scene_ids
        unknown_role_scenes = role_scene_ids - valid_scene_ids
        if missing_scene_roles:
            errors.append(
                "exploration scenes without explicit formula roles: "
                f"{sorted(missing_scene_roles)}"
            )
        if unknown_role_scenes:
            errors.append(
                "formula roles reference unknown exploration scenes: "
                f"{sorted(unknown_role_scenes)}"
            )
        role_covered_formula_ids: set[str] = set()
        for scene_id, role_definition in self.scene_formula_roles.items():
            if not isinstance(role_definition, dict):
                errors.append(f"scene {scene_id} has invalid formula roles")
                continue
            primary_ids = role_definition.get("primary_formula_ids")
            supporting_ids = role_definition.get("supporting_formula_ids", [])
            if not isinstance(primary_ids, list) or not primary_ids:
                errors.append(f"scene {scene_id} has no primary formula")
                primary_ids = []
            if not isinstance(supporting_ids, list):
                errors.append(f"scene {scene_id} has invalid supporting formulas")
                supporting_ids = []
            role_formula_ids = {str(item) for item in [*primary_ids, *supporting_ids]}
            role_covered_formula_ids.update(role_formula_ids.intersection(formula_ids))
            unknown_formula_ids = role_formula_ids - formula_ids
            if unknown_formula_ids:
                errors.append(
                    f"scene {scene_id} has unknown formula ids: "
                    f"{sorted(unknown_formula_ids)}"
                )
            duplicated_roles = set(primary_ids).intersection(supporting_ids)
            if duplicated_roles:
                errors.append(
                    f"scene {scene_id} assigns formulas to both roles: "
                    f"{sorted(duplicated_roles)}"
                )
            linked_formula_ids = {
                str(formula_id)
                for profile_id in self._formula_profile_ids_by_scene_id.get(
                    scene_id, []
                )
                for formula_id in [
                    *self._formula_profile_by_id.get(profile_id, {}).get(
                        "primary_formula_ids", []
                    ),
                    *self._formula_profile_by_id.get(profile_id, {}).get(
                        "supporting_formula_ids", []
                    ),
                ]
            }
            outside_linked_profiles = role_formula_ids - linked_formula_ids
            if outside_linked_profiles:
                errors.append(
                    f"scene {scene_id} uses formulas outside its linked profiles: "
                    f"{sorted(outside_linked_profiles)}"
                )
        formulas_without_scene_roles = formula_ids - role_covered_formula_ids
        if formulas_without_scene_roles:
            errors.append(
                "canonical formulas without an explicit scene role: "
                f"{sorted(formulas_without_scene_roles)}"
            )
        for lecture in self.lectures:
            start, end = lecture["pages"]
            if start > end or start < 1 or end > self.manifest["page_count"]:
                errors.append(f"invalid page range for {lecture['id']}")
        comparison_ids: set[str] = set()
        for comparison in self.baseline_comparisons:
            comparison_id = comparison.get("comparison_id")
            if not isinstance(comparison_id, str) or not comparison_id:
                errors.append("baseline comparison has an invalid id")
                continue
            if comparison_id in comparison_ids:
                errors.append(f"duplicate baseline comparison id: {comparison_id}")
            comparison_ids.add(comparison_id)
            cases = comparison.get("cases")
            if (
                not isinstance(cases, list)
                or not 2 <= len(cases) <= 4
                or any(not isinstance(case, str) or not case.strip() for case in cases)
            ):
                errors.append(f"baseline comparison {comparison_id} has invalid cases")
            trigger_groups = comparison.get("trigger_groups")
            if (
                not isinstance(trigger_groups, list)
                or not trigger_groups
                or any(not isinstance(group, list) or not group for group in trigger_groups)
            ):
                errors.append(
                    f"baseline comparison {comparison_id} has invalid trigger groups"
                )
            missing_scope = (
                set(comparison.get("course_concept_ids", [])) - valid_concept_ids
            )
            if missing_scope:
                errors.append(
                    f"baseline comparison {comparison_id} has unknown course scope ids: "
                    f"{sorted(missing_scope)}"
                )
        scene_template_ids: set[str] = set()
        visible_scene_signatures: set[str] = set()
        required_scene_fields = {
            "title",
            "physical_picture",
            "thinking_prompt",
            "illustrative_extension",
        }
        for template in self.scene_templates:
            template_id = template.get("template_id")
            if not isinstance(template_id, str) or not template_id:
                errors.append("scene template has an invalid id")
                continue
            if template_id in scene_template_ids:
                errors.append(f"duplicate scene template id: {template_id}")
            scene_template_ids.add(template_id)
            keywords = template.get("keywords")
            if (
                not isinstance(keywords, list)
                or not keywords
                or any(not isinstance(keyword, str) or not keyword.strip() for keyword in keywords)
            ):
                errors.append(f"scene template {template_id} has invalid keywords")
            if any(
                not isinstance(template.get(field), str) or not template[field].strip()
                for field in required_scene_fields
            ):
                errors.append(f"scene template {template_id} has an empty scene field")
            else:
                signature = self._scene_signature(template)
                if signature in visible_scene_signatures:
                    errors.append(
                        f"scene template {template_id} duplicates an existing visible frame"
                    )
                visible_scene_signatures.add(signature)
        if not self.generic_scene_frames:
            errors.append("scene templates have no generic fallback")
        for index, frame in enumerate(self.generic_scene_frames):
            if any(
                not isinstance(frame.get(field), str) or not frame[field].strip()
                for field in required_scene_fields
            ):
                errors.append(f"generic scene frame {index} has an empty scene field")
            else:
                signature = self._scene_signature(frame)
                if signature in visible_scene_signatures:
                    errors.append(
                        f"generic scene frame {index} duplicates an existing visible frame"
                    )
                visible_scene_signatures.add(signature)
        source_ids = set(self._supplemental_source_by_id)
        supplemental_ids: set[str] = set()
        for concept in self.supplemental_concepts:
            concept_id = concept["supplemental_concept_id"]
            if concept_id in supplemental_ids:
                errors.append(f"duplicate supplemental concept id: {concept_id}")
            supplemental_ids.add(concept_id)
            missing_scope = set(concept["course_scope_concept_ids"]) - valid_concept_ids
            if missing_scope:
                errors.append(
                    f"supplemental concept {concept_id} has unknown course scope ids: {sorted(missing_scope)}"
                )
            for option in concept["relationship_examples"]:
                if not option.get("direction") or not option.get("focus"):
                    errors.append(f"supplemental concept {concept_id} has an empty relationship")
                for reference in option["references"]:
                    source_id = reference["source_id"]
                    if source_id not in source_ids:
                        errors.append(
                            f"supplemental concept {concept_id} has unknown source: {source_id}"
                        )
                        continue
                    pages = reference["pdf_pages"]
                    page_count = self._supplemental_source_by_id[source_id]["page_count"]
                    if (
                        not isinstance(pages, list)
                        or len(pages) != 2
                        or pages[0] < 1
                        or pages[0] > pages[1]
                        or pages[1] > page_count
                    ):
                        errors.append(
                            f"supplemental concept {concept_id} has invalid pages for {source_id}: {pages}"
                        )
        expected_scene_count = sum(
            len(lecture.get("brainstorm_axes", [])) for lecture in self.lectures
        ) + sum(
            len(concept.get("relationship_examples", []))
            for concept in self.supplemental_concepts
        )
        if len(self.exploration_points) != expected_scene_count:
            errors.append(
                "exploration scene catalog does not cover every lecture axis and supplemental relation"
            )
        scene_catalog_ids: set[str] = set()
        scene_option_ids: set[str] = set()
        required_point_fields = {
            "catalog_scene_id",
            "catalog_scene_number",
            "option_id",
            "direction",
            "focus",
            "course_block",
            "catalog_source_type",
        }
        for expected_number, point in enumerate(self.exploration_points, start=1):
            if any(point.get(field) in (None, "") for field in required_point_fields):
                errors.append(f"exploration point {expected_number} has an empty field")
            scene_id = str(point.get("catalog_scene_id") or "")
            option_id = str(point.get("option_id") or "")
            if scene_id in scene_catalog_ids:
                errors.append(f"duplicate exploration scene id: {scene_id}")
            if option_id in scene_option_ids:
                errors.append(f"duplicate exploration option id: {option_id}")
            scene_catalog_ids.add(scene_id)
            scene_option_ids.add(option_id)
            if scene_id != f"ECE329-S{expected_number:03d}":
                errors.append(f"exploration scene id sequence is broken at {expected_number}")
        return errors


KNOWLEDGE = LectureKnowledgeBase()
