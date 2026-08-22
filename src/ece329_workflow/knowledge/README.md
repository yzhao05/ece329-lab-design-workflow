# ECE329 runtime knowledge catalog

This directory contains compact, deployable reference catalogs. Source PDFs are not bundled into the application image or committed to GitHub.

## Source hierarchy

1. `source_manifest.json` identifies `ece329lecture_notes.pdf`. The lecture notes define the ECE329 course boundary and the formulas that have been explicitly verified for formula-bearing stages.
2. `supplemental_sources.json` identifies verified supplemental references and stores paraphrased concept summaries, relationship examples, source sections, PDF page ranges, hashes, and mappings back to course-scope concept IDs.
3. Entries under `candidate_sources_not_used_for_retrieval` are bibliographic leads only. They are not included in model retrieval until a legal full text is supplied and its contents and pages are checked.

## Runtime files

- `concepts.json`: 39 lecture units, keywords, concepts, and lecture-grounded brainstorming axes.
- `formulas.json`: canonical formulas with IDs, conditions, course concept mappings, and PDF pages.
- `source_manifest.json`: identity and extraction policy for the course-scope lecture notes.
- `supplemental_sources.json`: enabled supplemental sources and multi-source Stage 1 relationship catalog.

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

Run the test suite after any catalog change. `LectureKnowledgeBase.validate()` checks IDs, course-scope mappings, source IDs, and PDF page bounds.
