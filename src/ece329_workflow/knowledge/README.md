# ECE329 runtime knowledge catalog

This directory contains compact, deployable reference catalogs. Source PDFs are not bundled into the application image or committed to GitHub.

## Source hierarchy

1. `source_manifest.json` identifies `ece329lecture_notes.pdf`. The lecture notes define the ECE329 course boundary and the formulas that have been explicitly verified for formula-bearing stages.
2. `supplemental_sources.json` identifies verified supplemental references and stores paraphrased concept summaries, relationship examples, source sections, PDF page ranges, hashes, and mappings back to course-scope concept IDs.
3. Entries under `candidate_sources_not_used_for_retrieval` are bibliographic leads only. They are not included in model retrieval until a legal full text is supplied and its contents and pages are checked.

## Runtime files

- `concepts.json`: 39 lecture units, keywords, concepts, lecture-grounded brainstorming axes, and an extensible catalog of basic comparison-case bundles. Each bundle carries course concept IDs; conversation code handles adoption and modification generically rather than branching on its topic.
- `formulas.json`: canonical formulas with IDs, conditions, course concept mappings, and PDF pages.
- `scene_templates.json`: extensible Stage 1 physical-scene templates selected by catalog keywords, plus topic-independent fallback frames. New course topics are added as data instead of Python conditionals.
- `source_manifest.json`: identity and extraction policy for the course-scope lecture notes.
- `supplemental_sources.json`: enabled supplemental sources and multi-source Stage 1 relationship catalog.

At startup, the workflow materializes a 138-item Stage 1 exploration catalog from all 117 lecture `brainstorm_axes` and all 21 supplemental `relationship_examples`. Entries receive stable internal IDs `ECE329-S001`–`ECE329-S138`. A turn samples three unseen entries, prioritizing the current topic, and relabels them only as `图景 A/B/C` for the student; internal IDs are never student-facing.

## Supplemental-source policy

- A supplemental concept must map to at least one valid `course_scope_concept_id`.
- A relationship example must cite an enabled source, section, and valid PDF page range.
- Stage 1 can use these relationships as examples and invite the student to suggest another relationship.
- Supplemental summaries do not authorize formula invention. Add a formula only through the separately reviewed formula-catalog process.
- Do not copy textbook prose into this catalog. Store short paraphrases, labels, and locators only.

## Local source files used for the current extraction

The following files were inspected under `E:\暑研\新参考资料` and are intentionally outside the repository:

- `BOOK_ElectromagneticWaveTheory_by_JINAUKONG_FreeRelease2021.pdf`
- `EMandApp_DavidH.pdf`
- `getfile.asp` (a PDF despite its extension)

Run the test suite after any catalog change. `LectureKnowledgeBase.validate()` checks IDs, course-scope mappings, source IDs, PDF page bounds, comparison bundles, and scene-template completeness.
