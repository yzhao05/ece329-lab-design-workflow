"""Canonical catalog selections shared by EMVR dialogue and report views."""
from copy import deepcopy
import re

from .knowledge_base import KNOWLEDGE
from .models import Stage

CATALOG_FIELDS = {'primary_course_concept_id', 'course_reference_ids', 'primary_formula_ids', 'supporting_formula_ids'}


def display_catalog_value(field, value):
    if field not in CATALOG_FIELDS:
        return None
    records = KNOWLEDGE.lectures if 'course' in field else KNOWLEDGE.formulas
    names = {r['id']: r.get('title', r.get('name', '')) for r in records}
    values = value if isinstance(value, list) else [value] if value else []
    return '、'.join(names[v] for v in values if isinstance(v, str) and v in names) or ('无' if value == [] else '')


def visible_core_ids(session):
    displayed = session.stage_outputs.get(Stage.THEORETICAL_FRAMEWORK.value, {}).get('stage_payload', {}).get('core_equations', [])
    start = session.model_context.get('design_history_start', 0)
    start = start if isinstance(start, int) and start >= 0 else 0
    for entry in reversed(session.history[start:]):
        if entry.get('resolved_intent', {}).get('intent') == 'NEW_TOPIC':
            break
        payload = entry.get('output', {}).get('stage_payload', {})
        if 'core_equations' in payload:
            displayed = payload['core_equations']
            break
    known = {f['id'] for f in KNOWLEDGE.formulas}
    return [f['id'] for f in displayed if isinstance(f, dict) and f.get('id') in known]


def selection(emvr, role):
    field = role + '_formula_ids'
    cleared = emvr.get('explicitly_cleared_fields', [])
    if field in cleared:
        return []
    flow = emvr.get('formula_flow', {})
    sources = [emvr.get('field_state', {}),
               {field: emvr['selected_' + field]} if 'selected_' + field in emvr else {},
               emvr.get('authoritative_experiment_brief', {}),
               flow.get('experiment_brief', {}), flow.get('formula_selection', {})]
    for source in sources:
        if isinstance(source, dict) and field in source:
            return list(source[field]) if isinstance(source[field], list) else []
    legacy = flow.get('formula_selection', {}).get(role, [])
    return list(legacy) if isinstance(legacy, list) else []


def sync_selection(emvr, touched):
    """Do not let any old brief resurrect a removed/reclassified formula."""
    if not touched & {'primary_formula_ids', 'supporting_formula_ids'}:
        return
    primary, supporting = selection(emvr, 'primary'), selection(emvr, 'supporting')
    # Moving into a role removes the previous role unless both were explicit.
    if 'supporting_formula_ids' in touched and 'primary_formula_ids' not in touched:
        primary = [v for v in primary if v not in supporting]
    else:
        supporting = [v for v in supporting if v not in primary]
    for role, values in [('primary', primary), ('supporting', supporting)]:
        field = role + '_formula_ids'
        emvr.setdefault('field_state', {})[field] = values
        emvr['selected_' + field] = values
        for container in [emvr.get('authoritative_experiment_brief'),
                          emvr.get('formula_flow', {}).get('experiment_brief'),
                          emvr.get('formula_flow', {}).get('formula_selection')]:
            if isinstance(container, dict):
                container[field] = deepcopy(values)
        selection_state = emvr.get('formula_flow', {}).get('formula_selection', {})
        if role in selection_state:
            selection_state[role] = deepcopy(values)
    formulas = {f['id']: f for f in KNOWLEDGE.formulas}
    selected = set(primary + supporting)
    for method in emvr.get('formula_flow', {}).get('experiment_methods', []):
        if not isinstance(method, dict):
            continue
        method['primary_formula_ids'] = list(primary)
        method['supporting_formula_ids'] = list(supporting)
        if isinstance(method.get('formula_pattern_assignments'), list):
            method['formula_pattern_assignments'] = [a for a in method['formula_pattern_assignments']
                                                     if a.get('formula_id') in selected]
        description = str(method.get('description') or '')
        if '研究' in description:
            method['description'] = description.split('研究', 1)[0] + '研究' + '、'.join(
                f"{formulas[f]['name']}（{formulas[f]['expression']}）" for f in primary + supporting if f in formulas) + '。'


def course_projection(emvr):
    formulas = {f['id']: f for f in KNOWLEDGE.formulas}
    state = emvr.get('field_state', {})
    ids = list(dict.fromkeys(c for fid in selection(emvr, 'primary') + selection(emvr, 'supporting')
                            for c in formulas.get(fid, {}).get('concept_ids', [])))
    if 'course_reference_ids' in state:
        ids = state['course_reference_ids']
    if 'course_reference_ids' in emvr.get('explicitly_cleared_fields', []):
        ids = []
    refs = KNOWLEDGE.concept_references_for_ids(ids)
    if not refs:
        return {}
    anchor = state.get('primary_course_concept_id')
    primary = next((r for r in refs if r['concept_id'] == anchor), refs[0])
    return {'primary_topic': primary['title'],
            'secondary_topics': [r['title'] for r in refs if r['concept_id'] != primary['concept_id']],
            'course_references': refs}


def project_catalog(session, stage, payload):
    from .models import InteractionState
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return
    emvr = session.design_context.get('emvr_design', {})
    if stage is Stage.IDEA_BRAINSTORMING and emvr.get('field_state', {}).get('path_shape_options'):
        payload['path_shape_options'] = deepcopy(emvr['field_state']['path_shape_options'])
    elif stage is Stage.COURSE_MAPPING_AND_DIRECTION:
        projection = course_projection(emvr)
        if projection:
            payload.update(projection)
        elif ('course_reference_ids' in emvr.get('field_state', {})
              or 'course_reference_ids' in emvr.get('explicitly_cleared_fields', [])):
            payload.update(primary_topic='', secondary_topics=[], course_references=[])
    elif stage is Stage.THEORETICAL_FRAMEWORK and ('selected_primary_formula_ids' in emvr or
                                                   'primary_formula_ids' in emvr.get('field_state', {})):
        formulas = {f['id']: f for f in KNOWLEDGE.formulas}
        payload['core_equations'] = [deepcopy(formulas[f]) for f in selection(emvr, 'primary') if f in formulas]
        payload['supporting_equations'] = [deepcopy(formulas[f]) for f in selection(emvr, 'supporting') if f in formulas]


def recover_catalog_edits(session, message):
    """Only resolve explicit catalog names/visible ordinals, never free formulas."""
    from .procedure_contract import has_positive_action
    text = message.strip().strip('*')
    emvr = session.design_context.get('emvr_design', {})
    edits = {}
    def put(field, value):
        edits[field] = {'operation': 'REPLACE' if value else 'CLEAR', 'value': value}
    concepts = KNOWLEDGE.concept_references_for_ids([c['id'] for c in KNOWLEDGE.lectures])
    # A mismatch report already identifies both rows: repair their projection
    # from selected formulas, rather than clearing an unrelated explanation.
    if ('主要课程主题' in text and re.search(r'讲义|课程.*依据', text)
            and re.search(r'对不上|不一致|不是同一|不匹配', text)):
        projection = course_projection(emvr)
        refs = projection.get('course_references', [])
        if refs:
            put('primary_course_concept_id', refs[0]['concept_id'])
            put('course_reference_ids', [r['concept_id'] for r in refs])
    for clause in re.split(r'[；;。\n]', text):
        if '主要课程主题' in clause and has_positive_action(clause, r'改为|改成|替换为'):
            matched = [c for c in concepts if c['title'].casefold() in clause.casefold()]
            if len(matched) == 1:
                put('primary_course_concept_id', matched[0]['concept_id'])
                # A heading must always be represented by its own source.
                refs = course_projection(emvr).get('course_references', [])
                put('course_reference_ids', list(dict.fromkeys([matched[0]['concept_id'], *[r['concept_id'] for r in refs]])))
        if re.search(r'(?:课程)?讲义依据', clause) and has_positive_action(clause, r'只保留|改为|改成'):
            nums = re.findall(r'(?:Lecture\s*|第\s*)(\d+)', clause, re.I)
            refs = [c for c in concepts if c['concept_id'] in {f'lecture_{n.zfill(2)}' for n in nums}]
            if refs and len(refs) == len(set(nums)):
                put('course_reference_ids', [c['concept_id'] for c in refs])
                put('primary_course_concept_id', refs[0]['concept_id'])
    # Bind formula indices to what was last displayed, including legacy replies
    # that incorrectly put supporting equations in the core list.
    visible_ids = visible_core_ids(session) or selection(emvr, 'primary')
    ordinal = re.search(r'第\s*([一二三四五六七八九十]|\d+)\s*条', text)
    if ordinal and not re.search(r'[？?]|能算|可以吗|合适吗', text):
        token = ordinal.group(1)
        index = int(token) if token.isdigit() else '一二三四五六七八九十'.index(token)+1
        if 0 < index <= len(visible_ids):
            fid = visible_ids[index-1]
            if has_positive_action(text, r'放到辅助公式|放在支撑公式|放到支撑公式|移到辅助公式'):
                put('primary_formula_ids', [v for v in selection(emvr, 'primary') if v != fid])
                put('supporting_formula_ids', list(dict.fromkeys(selection(emvr, 'supporting') + [fid])))
            elif re.search(r'核心公式', text) and has_positive_action(text, r'删除|去掉|移除'):
                put('primary_formula_ids', [v for v in selection(emvr, 'primary') if v != fid])
                put('supporting_formula_ids', [v for v in selection(emvr, 'supporting') if v != fid])
    for role, label in [('primary', '核心公式|主要公式'), ('supporting', '支撑公式|辅助公式')]:
        match = re.search(rf'(?:{label})(?:改为|改成|替换为)\s*[：:]\s*(.+?)'
                          r'(?=[；;，,\n]\s*(?:核心公式|主要公式|辅助公式|支撑公式)(?:改为|改成|替换为)|$)', text, re.S)
        if match and has_positive_action(text, re.escape(match.group(0))):
            named = match.group(1).casefold()
            values = [fid for _,fid in sorted((named.index(f['name'].casefold()), f['id'])
                      for f in KNOWLEDGE.formulas if f['name'].casefold() in named)]
            if values:
                put(role + '_formula_ids', values)
    return edits
