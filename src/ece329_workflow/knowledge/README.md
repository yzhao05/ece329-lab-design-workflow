# ECE329 runtime knowledge catalog

This directory contains compact, deployable reference catalogs. Source PDFs are not bundled into the application image or committed to GitHub.

## Source hierarchy

1. `source_manifest.json` identifies `ece329lecture_notes.pdf`. The lecture notes define the ECE329 course boundary and the formulas that have been explicitly verified for formula-bearing stages.
2. `supplemental_sources.json` identifies verified supplemental references and stores paraphrased concept summaries, relationship examples, source sections, PDF page ranges, hashes, and mappings back to course-scope concept IDs.
3. Entries under `candidate_sources_not_used_for_retrieval` are bibliographic leads only. They are not included in model retrieval until a legal full text is supplied and its contents and pages are checked.

## Runtime files

- `concepts.json`: 39 lecture units, keywords, concepts, lecture-grounded brainstorming axes, and an extensible catalog of basic comparison-case bundles. Each bundle carries course concept IDs; conversation code handles adoption and modification generically rather than branching on its topic.
- `formulas.json`: canonical formulas with IDs, conditions, course concept mappings, and PDF pages.
- `formula_design_profiles.json`: formula-centered experiment-design profiles. Each
  profile distinguishes primary and supporting formulas and records supported
  variations, observations, and boundary conditions; all formula IDs resolve back to
  `formulas.json`.
- `scene_formula_links.json`: legacy exploration scene bindings to formula profiles.
  The runtime merges these with the authored Guided extension before resolving
  selections and filtering the sampling pool by course domain.
- `experiment_design_patterns.json`: 15 finite experiment-design paradigms and an
  explicit applicability map for every formula profile. EMVR uses this layer to
  generate a coverage matrix and runtime experiment methods after formula
  confirmation; it does not sample the fixed scene catalog.
- `scene_templates.json`: extensible Stage 1 physical-scene templates selected by catalog keywords, plus topic-independent fallback frames. New course topics are added as data instead of Python conditionals.
- `guided_formula_pattern_scenes.json`: 45 authored Guided scenes covering all 32
  formula profiles and all 15 applicable experiment paradigms. Each declares its
  primary formulas, profile, pattern, distinctive question and complete physical
  picture. Supporting profiles add explicit cross-formula evidence where needed.
  These scenes use their own bound picture rather than a generic keyword fallback.
- `source_manifest.json`: identity and extraction policy for the course-scope lecture notes.
- `supplemental_sources.json`: enabled supplemental sources and multi-source Stage 1 relationship catalog.

At startup, the workflow materializes a **183-item Guided Stage 1 exploration catalog**:
117 lecture axes + 21 supplemental relationships + 45 formula-pattern scenes.
Legacy IDs `ECE329-S001`–`ECE329-S138` retain their meaning; new IDs are
`ECE329-S139`–`ECE329-S183`. IDs are append-only. Each turn still presents three
scenes, labeled A/B/C, with no internal IDs in student-facing text. The additions
are selected applicable combinations, not an exhaustive 32 × 15 product: simply
changing wording or attaching another paradigm label is not a new scene.

Sampling prioritizes the current topic and exhausts unseen candidates in the
eligible course domain before recycling. If one or two unseen entries remain,
they appear first, followed by the start of a new cycle; a cycle marker in the
stored alternatives resets only that pool's exclusions. The global pool supports
61 three-scene batches per cycle. Counts in prompt metadata and public formula
links derive from the merged catalog. EMVR retains its formula-first dynamic
method generation and does not sample this catalog.

To extend the pool, append a scene with a new stable ID, an applicable profile–pattern
pair and a distinct physical question. Reuse canonical formula IDs and conditions;
do not introduce unsupported quantitative claims. Validation checks references,
applicability, IDs and duplicate titles/pictures/questions; editorial review must
also check semantic overlap with existing lecture axes and scenes.

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
