from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


class LectureKnowledgeBase:
    def __init__(self) -> None:
        root = files("ece329_workflow").joinpath("knowledge")
        self.manifest = json.loads(root.joinpath("source_manifest.json").read_text(encoding="utf-8"))
        self.concept_data = json.loads(root.joinpath("concepts.json").read_text(encoding="utf-8"))
        self.formula_data = json.loads(root.joinpath("formulas.json").read_text(encoding="utf-8"))
        self.lectures: list[dict[str, Any]] = self.concept_data["lectures"]
        self.formulas: list[dict[str, Any]] = self.formula_data["formulas"]
        self._lecture_by_id = {item["id"]: item for item in self.lectures}

    @property
    def source_reference(self) -> dict[str, Any]:
        return {
            "source_id": self.manifest["source_id"],
            "title": self.manifest["title"],
            "sha256": self.manifest["sha256"],
            "page_count": self.manifest["page_count"],
        }

    def match_concepts(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        normalized = text.casefold()
        scored: list[tuple[int, dict[str, Any]]] = []
        for lecture in self.lectures:
            score = 0
            for keyword in lecture["keywords"]:
                keyword_normalized = keyword.casefold()
                if keyword_normalized and keyword_normalized in normalized:
                    score += max(2, len(keyword_normalized.split()))
            for concept in lecture["concepts"]:
                if concept.casefold() in normalized:
                    score += 3
            if score:
                scored.append((score, lecture))
        scored.sort(key=lambda item: (-item[0], item[1]["lecture"]))
        return [item for _, item in scored[:limit]]

    def broad_entry_points(self) -> list[dict[str, Any]]:
        overview = self.concept_data["overview"]
        descriptions = {
            "electrostatics": "从静电场、电势、介质、极化或电容等讲义概念开始发散",
            "magnetism": "从磁力、安培定律、磁通、感应或电感等讲义概念开始发散",
            "electromagnetics": "从Maxwell方程、电磁波、偏振、反射或传输线等讲义概念开始发散",
        }
        return [
            {
                "direction": block["label"],
                "focus": descriptions[block["id"]],
                "source_pages": overview["pages"],
                "source_lectures": block["lectures"],
                "concept_id": block["id"],
            }
            for block in overview["course_blocks"]
        ]

    def brainstorm_options(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        matches = self.match_concepts(text, limit=3)
        if not matches:
            return self.broad_entry_points()[:limit]
        options: list[dict[str, Any]] = []
        for lecture in matches:
            for axis in lecture["brainstorm_axes"]:
                options.append(
                    {
                        "direction": lecture["title"],
                        "focus": axis,
                        "concept_id": lecture["id"],
                        "source_lecture": lecture["lecture"],
                        "source_pages": lecture["pages"],
                    }
                )
                if len(options) >= limit:
                    return options
        return options

    def concept_references(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        matches = self.match_concepts(text, limit=limit)
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
            return []
        matches = [
            formula
            for formula in self.formulas
            if concept_ids.intersection(formula["concept_ids"])
        ]
        return matches[:limit]

    def public_concepts(self) -> list[dict[str, Any]]:
        return self.lectures

    def public_formulas(self) -> list[dict[str, Any]]:
        return self.formulas

    def search(self, text: str) -> dict[str, Any]:
        concepts = self.concept_references(text, limit=5)
        return {
            "query": text,
            "source": self.source_reference,
            "concepts": concepts,
            "formulas": self.formula_references(text, limit=12),
            "brainstorm_options": self.brainstorm_options(text, limit=3),
            "fallback_used": not bool(concepts),
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
        return errors


KNOWLEDGE = LectureKnowledgeBase()
