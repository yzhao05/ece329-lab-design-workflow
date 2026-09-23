# Dialogue execution evidence

Guided and EMVR share the same observers. They do not invoke a model, retry an
operation, approve a candidate, or bypass stage validation.

New dialogue turns retain version 1 `execution_diagnostic` records in private
session model context (latest 40 turns) and the existing server telemetry store.
Feedback captures the diagnostic for the exact reported revision, including in
the existing authenticated evidence export. Public dialogue/history responses
and the design-owner telemetry endpoint do not expose these diagnostics.
Older turns without a matching record remain unknown; submission-time state is
never substituted for missing execution evidence.

Recorded observations include:

- Parsed model envelope, normalized model result, resolver output, and each
  `validate_resolved_intent` input/output separately.
- Action rejection index, validator, rule, and code; empty authoritative actions,
  disallowed intent and insufficient confidence have explicit codes.
- Candidate identities (pending ID plus content fingerprint), authorization,
  retained-turn count, before/after status, and the function making the change.
- Canonical field changes and low-level design/stage field processing: invalid
  field/operation, empty value, duplicate update, already saved, or applied to
  memory. Memory application does not establish database persistence.
- `_validate_completion` success, incomplete-stage exception or backend exception.
  Its absence is reported only for this named checker, not every possible check.
- Selected clarification template and its pending ID/conditions, generator
  invocation, and recovery decisions, including unbound multi-field candidates.
- Original serialized context keys and the keys submitted to the provider adapter;
  omitted keys, request size and output cap. This does not claim to measure what
  a provider internally retained. Historical turn attribution that is not
  available remains null.
- Existing experience retrieval/injection/action/verification events, plus
  repeated reply and unchanged canonical-design/stage counts. Pending-only
  changes are outside that repetition comparison scope.

The UI shows a short bilingual summary and four independently collapsed detail
groups. Technical values remain original evidence. Review drafts and existing
fold state survive re-rendering and language changes. All values use text nodes.

Action contents are represented by fingerprints, lengths, types and recorded
source offsets, rather than duplicating conversation text in usage logs. The
existing conversation evidence retains the original text. No request headers,
API credentials or model-internal reasoning are captured. Diagnostic strings
are bounded and credential-shaped values are redacted. Backend exception
messages are not saved; checker-authored completion explanations are retained.

The event cap is 120 events and a 60,000-character serialized event budget per
turn. Individual values also have bounds. Truncation is explicit and must not
be interpreted as nonexecution. Extraction receives one diagnostic copy per
known revision and at most 18,000 characters of diagnostic content, including
state summaries and duplicate references. Failed or blocked events take priority;
omission counts and metadata truncation are explicit. Unknown revisions are not
deduplicated as if they were the same turn. Raw evidence is unchanged.
These limits do not increase extraction attempts, recovery attempts or API calls.

Progress distinguishes forward stage movement or workflow completion from
backward stage normalization. Recovery observers tolerate rejected malformed
pending values without changing the underlying safe rejection into an exception.

Deployment requires the updated backend and `docs` frontend. There is no SQL
schema migration or new configuration requirement. `ECE329_RELEASE_VERSION`,
`RENDER_GIT_COMMIT` or `GITHUB_SHA` supplies the backend revision when available;
otherwise it is recorded as null. This change adds execution evidence, not a
replacement for the multi-field recovery policy or a proof that all loops are
fixed. Simulated tests must not be reported as live model validation.
