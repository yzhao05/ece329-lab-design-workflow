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
        self.supplemental_sources: list[dict[str, Any]] = self.supplemental_data["sources"]
        self.supplemental_concepts: list[dict[str, Any]] = self.supplemental_data["concepts"]
        self.scene_templates: list[dict[str, Any]] = self.scene_template_data["templates"]
        self.generic_scene_frames: list[dict[str, Any]] = self.scene_template_data[
            "generic_frames"
        ]
        self._lecture_by_id = {item["id"]: item for item in self.lectures}
        self._supplemental_source_by_id = {
            item["source_id"]: item for item in self.supplemental_sources
        }
        self._supplemental_concept_by_id = {
            item["supplemental_concept_id"]: item for item in self.supplemental_concepts
        }
        self.exploration_points = self._build_exploration_points()

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

    def _relevant_exploration_points(self, text: str) -> list[dict[str, Any]]:
        lecture_matches = self.match_concepts(text, limit=len(self.lectures))
        supplemental_matches = self.match_supplemental_concepts(
            text,
            limit=len(self.supplemental_concepts),
        )
        if not lecture_matches and not supplemental_matches:
            return list(self.exploration_points)

        lecture_ids = {str(item["id"]) for item in lecture_matches}
        supplemental_ids = {
            str(item["supplemental_concept_id"]) for item in supplemental_matches
        }
        for concept in supplemental_matches:
            lecture_ids.update(str(item) for item in concept["course_scope_concept_ids"])
        return [
            point
            for point in self.exploration_points
            if str(point.get("concept_id") or "") in lecture_ids
            or str(point.get("supplemental_concept_id") or "") in supplemental_ids
        ]

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
    ) -> list[dict[str, Any]]:
        """Sample unique, course-grounded points while avoiding previously shown ones."""

        if limit <= 0:
            return []
        excluded = {str(item) for item in (exclude_option_ids or set()) if str(item)}
        relevant = self._relevant_exploration_points(text)
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
            )
        if len(remaining) < limit:
            remaining = list(relevant or self.exploration_points)

        rng = random.Random(self._sample_seed(seed_key, len(excluded)))
        no_specific_match = len(relevant) == len(self.exploration_points)
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

    def public_concepts(self) -> list[dict[str, Any]]:
        return self.lectures

    def public_supplemental_concepts(self) -> list[dict[str, Any]]:
        return self.supplemental_concepts

    def public_formulas(self) -> list[dict[str, Any]]:
        return self.formulas

    def search(self, text: str) -> dict[str, Any]:
        concepts = self.concept_references(text, limit=5)
        supplemental = self.supplemental_concept_references(text, limit=5)
        return {
            "query": text,
            "source": self.source_reference,
            "course_scope_source": self.source_reference,
            "sources": self.source_references,
            "concepts": concepts,
            "supplemental_concepts": supplemental,
            "formulas": self.formula_references(text, limit=12),
            "brainstorm_options": self.brainstorm_options(text, limit=3),
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
