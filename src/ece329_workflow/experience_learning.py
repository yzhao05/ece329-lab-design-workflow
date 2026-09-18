"""Bounded evidence diagnostics and maintainer correction contracts.

These records improve retrieval-time examples, never model weights. Model case
checks are explicitly not execution of the workflow or independent verification.
"""
from copy import deepcopy
import json

REVIEW_FIELDS = {'summary': 600, 'trigger': 140, 'recommendation': 260, 'verification': 120}


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def diagnosis_schema():
    text = {'type': 'string', 'minLength': 1, 'maxLength': 1000}
    texts = {'type': 'array', 'items': text, 'maxItems': 6}
    case = object_schema({'input': text, 'expected': text})
    return object_schema({
        'facts': {'type': 'array', 'maxItems': 6, 'items': object_schema({'evidence_ref': text, 'observation': text})},
        'user_report': text, 'expected_behavior': text,
        'hypotheses': texts, 'unknowns': texts,
        'applicability': text, 'exceptions': text,
        'positive_case': case, 'negative_case': case,
    })


def check_schema():
    return object_schema({
        'evidence_supported': {'type': 'boolean'},
        'positive_case_passes': {'type': 'boolean'},
        'negative_case_passes': {'type': 'boolean'},
        'issues': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
    })


def validate_shape(value, schema, *, limit=1000):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise ValueError('Invalid experience analysis fields')
        for key, spec in schema['properties'].items():
            validate_shape(value[key], spec, limit=limit)
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > 6:
            raise ValueError('Invalid experience analysis array')
        for item in value:
            validate_shape(item, schema['items'], limit=limit)
    elif kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('Invalid experience check')
    elif not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid experience analysis text')
    return deepcopy(value)


def validate_review_note(note):
    # Keep existing structured reviews readable; new writes use opinion.
    if isinstance(note, dict) and set(note) == {'field', 'original', 'corrected', 'basis'}:
        note = {**note, 'opinion': note['basis']}
        del note['basis']
    if not isinstance(note, dict) or set(note) not in (
            {'original', 'corrected', 'opinion'}, {'field', 'original', 'corrected', 'opinion'}):
        raise ValueError('Review requires original, corrected and opinion; reload the review page')
    if 'field' in note and (not isinstance(note['field'], str) or note['field'] not in REVIEW_FIELDS):
        raise ValueError('Invalid review correction field')
    # Freeform annotations can describe several fields. They never patch the rule.
    for key in ('original', 'corrected', 'opinion'):
        if not isinstance(note[key], str) or len(note[key]) > 2000:
            raise ValueError('Review text must be a string of at most 2000 characters')
    if not note['opinion'].strip():
        raise ValueError('Review requires a handling opinion')
    if bool(note['original'].strip()) != bool(note['corrected'].strip()):
        raise ValueError('Provide both original and corrected text, or leave both blank if no correction is needed')
    return {key: value.strip() for key, value in note.items()}


def workflow_evidence_state(session):
    # Read only: capturing evidence must not initialise or migrate dialogue state.
    dialogue = session.model_context.get('dialogue_state')
    pending = dialogue.get('pending_action') if isinstance(dialogue, dict) else None
    state = session.design_context.get('design_state', {})
    state = state if isinstance(state, dict) else {}
    provenance = state.get('field_provenance', {})
    provenance = provenance if isinstance(provenance, dict) else {}
    latest = {field: rows[-1] for field, rows in provenance.items() if isinstance(rows, list) and rows}
    return {
        'revision': session.revision, 'stage': session.current_stage.value,
        'completed_stages': list(session.completed_stages),
        # This is a bounded excerpt, explicitly not a complete design snapshot.
        'pending_excerpt': json.dumps(pending, ensure_ascii=False)[:2000],
        'confirmation_excerpt': json.dumps({'topic_lock': state.get('topic_lock'),
                                            'latest_field_provenance': latest}, ensure_ascii=False)[:4000],
    }
