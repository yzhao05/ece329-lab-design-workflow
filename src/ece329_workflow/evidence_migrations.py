"""One-time, local evidence upgrades. Never rewrite learned rules or reviews."""
from copy import deepcopy
import json
import time


def _object(value):
    return value if isinstance(value, dict) else {}


def upgrade_payload(payload, history):
    """Recover only matching historical evidence; submission state is never reused."""
    from .experience import _turn_evidence
    result = deepcopy(payload)
    evidence = result.get('evidence')
    if not isinstance(evidence, dict) or evidence.get('evidence_schema_version', 0) >= 2:
        return result, False
    limit = _object(evidence.get('source')).get('revision', result.get('submission_revision'))
    # Exclude turns made after the feedback was submitted. If that boundary was
    # not recorded, existing target/neighbor references are the only safe inputs.
    historical = [row for row in history if isinstance(row, dict)
                  and isinstance(row.get('revision'), int)
                  and (not isinstance(limit, int) or row['revision'] <= limit)]
    recovered, conflicts = [], []

    def match(old):
        candidates = [row for row in historical if row.get('revision') == old.get('revision')
                      and (not old.get('stage') or row.get('handled_stage') == old['stage'])
                      and (not old.get('mode') or row.get('interaction_state') == old['mode'])]
        return candidates[0] if len(candidates) == 1 else None

    def normalize(old, user_limit=2000, assistant_limit=4000):
        if not isinstance(old, dict):
            return old
        row = deepcopy(old)
        source = match(row)
        metadata = _object(row.get('recorded_fields')).copy()
        truncated = list(row.get('truncated_fields') or [])
        unknown = []
        fresh = _turn_evidence(source, user_limit, assistant_limit) if source else {}
        for field in ('user', 'assistant', 'student_task', 'warnings', 'assumptions'):
            if source and (field not in row or row[field] == fresh.get(field)):
                if field in fresh:
                    row.setdefault(field, fresh[field])
                metadata.setdefault(field, fresh['recorded_fields'][field])
                if field in fresh['truncated_fields'] and field not in truncated:
                    truncated.append(field)
                recovered.append(f"turn:{row.get('revision')}:{field}")
            elif field not in metadata:
                if field not in row or row[field] is None:
                    metadata[field] = False
                elif row[field] != '':
                    metadata[field] = True
                # Empty legacy strings remain ambiguous, never marked as an
                # original empty reply without matching historical evidence.
                unknown.append(field)
                if source:
                    conflicts.append(f"turn:{row.get('revision')}:{field}")
        row['recorded_fields'] = metadata
        row['truncated_fields'] = truncated
        if unknown:
            row['truncation_unknown_fields'] = unknown
        if source:
            for field, source_key in [('state_before', 'feedback_state_before'),
                                      ('state_after', 'feedback_state_after'), ('resolved_intent', 'resolved_intent')]:
                if row.get(field) is None and source.get(source_key) is not None:
                    row[field] = deepcopy(source[source_key])
                    recovered.append(f"turn:{row.get('revision')}:{field}")
            for field, source_key in [('stage', 'handled_stage'), ('mode', 'interaction_state')]:
                if row.get(field) is None and source.get(source_key) is not None:
                    row[field] = source[source_key]
        return row

    chain = evidence.get('event_chain')
    if not chain and isinstance(limit, int):
        revision = result.get('reported_revision', _object(evidence.get('reported_turn')).get('revision'))
        stage = result.get('reported_stage', _object(evidence.get('reported_turn')).get('stage'))
        target = match({'revision': revision, 'stage': stage})
        if target:
            index = historical.index(target)
            chain = [{'revision': row['revision'], 'stage': row.get('handled_stage'),
                      'position': 'reported' if i == index else 'before' if i < index else 'after',
                      'ref': f"turn:{row['revision']}"}
                     for i, row in enumerate(historical) if index - 1 <= i <= index + 1]
            original_target = _object(evidence.get('reported_turn'))
            for row in chain:
                if row['position'] == 'reported' and original_target.get('revision') == row['revision']:
                    # Prefer the captured problem-turn excerpt when constructing
                    # a missing chain; conflicts must not disappear from the UI.
                    row.update({key: deepcopy(value) for key, value in original_target.items()
                                if key not in {'position', 'ref'}})
    if isinstance(chain, list):
        evidence['event_chain'] = [normalize(row) for row in chain]
    if isinstance(evidence.get('reported_turn'), dict):
        evidence['reported_turn'] = normalize(evidence['reported_turn'], 1500)
    if isinstance(evidence.get('recent_turns'), list):
        evidence['recent_turns'] = [normalize(row, 1500, 2000) for row in evidence['recent_turns']]
    evidence['evidence_schema_version'] = 2
    evidence['evidence_migration'] = {
        'version': 1, 'source': 'legacy_saved_evidence',
        'recovered_fields': sorted(set(recovered)), 'conflicting_fields_preserved': sorted(set(conflicts)),
        'limitations': 'Only matching saved historical turns were used. Missing original states and truncation flags remain unknown. No model analysis or replay was performed.',
    }
    return result, True


def migrate_evidence(db):
    """Atomic, serialized and idempotent across process restarts and workers."""
    db.execute('BEGIN IMMEDIATE')
    db.execute('''CREATE TABLE IF NOT EXISTS experience_evidence_migrations (
        ticket_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
        previous_payload TEXT, outcome TEXT NOT NULL, migrated_at REAL NOT NULL)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS feedback_migration_deleted
        AFTER DELETE ON feedback_tickets BEGIN
        DELETE FROM experience_evidence_migrations WHERE ticket_id=OLD.id;
        END''')
    has_sessions = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='design_sessions'").fetchone())
    summary = {'migrated': 0, 'current': 0, 'unavailable': 0}
    rows = db.execute('''SELECT t.id,t.design_id,t.payload FROM feedback_tickets t
        LEFT JOIN experience_evidence_migrations m ON m.ticket_id=t.id
        WHERE m.ticket_id IS NULL ORDER BY t.design_id,t.id''')
    design_id, history = None, []
    for row in rows:
        if row['design_id'] != design_id:
            design_id, history = row['design_id'], []
            session = db.execute('SELECT payload FROM design_sessions WHERE design_id=?', (design_id,)).fetchone() if has_sessions else None
            if session:
                try:
                    saved = json.loads(session['payload'])
                    if isinstance(saved.get('history'), list):
                        history = saved['history']
                except (ValueError, TypeError, AttributeError):
                    pass  # Keep evidence readable even if original history is unavailable.
        outcome, previous = 'unavailable', None
        try:
            payload = json.loads(row['payload'])
            if isinstance(payload, dict) and isinstance(payload.get('evidence'), dict):
                upgraded, changed = upgrade_payload(payload, history)
                outcome = 'migrated' if changed else 'current'
                if changed:
                    previous = row['payload']
                    db.execute('UPDATE feedback_tickets SET payload=? WHERE id=?',
                               (json.dumps(upgraded, ensure_ascii=False, sort_keys=True), row['id']))
        except (ValueError, TypeError):
            # Never rewrite malformed legacy JSON or log source content.
            pass
        db.execute('INSERT INTO experience_evidence_migrations VALUES(?,?,?,?,?)',
                   (row['id'], 1, previous, outcome, time.time()))
        summary[outcome] += 1
    return summary
