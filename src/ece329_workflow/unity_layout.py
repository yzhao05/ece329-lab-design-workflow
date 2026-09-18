"""Read-only, conservative projection of current EMVR facts into a method diagram.

No formula-derived objects, Builder defaults, historical text or model calls.
Only explicit local placement clauses are drawable; other prose stays in the
summary for review instead of being guessed into scene coordinates.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .emvr_design import merge_emvr_structured_requirements
from .models import InteractionState


def _texts(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return list(dict.fromkeys(x.strip() for x in value if isinstance(x, str) and x.strip()))
    return []


RELATIONS = {
    '左侧': 'left', '左边': 'left', '右侧': 'right', '右边': 'right',
    '前方': 'front', '后方': 'back', '圆心': 'center', '中心': 'center',
    'left': 'left', 'right': 'right', 'front': 'front', 'back': 'back', 'center': 'center',
}


def _has_position_cycle(nodes, links):
    """Check ordering constraints without assigning coordinates or recursing."""
    parents = {node['id']: node['id'] for node in nodes}

    def root(value):
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    for link in links:
        if link['relation'] == 'center':
            parents[root(link['source'])] = root(link['target'])
    for forward, reverse in [('left', 'right'), ('front', 'back')]:
        graph = {root(value): set() for value in parents}
        for link in links:
            if link['relation'] not in (forward, reverse):
                continue
            a, b = root(link['source']), root(link['target'])
            if link['relation'] == reverse:
                a, b = b, a
            graph[a].add(b)
        degrees = {value: 0 for value in graph}
        for targets in graph.values():
            for value in targets:
                degrees[value] += 1
        ready = [value for value in graph if not degrees[value]]
        count = 0
        while ready:
            value = ready.pop(); count += 1
            for target in graph[value]:
                degrees[target] -= 1
                if not degrees[target]:
                    ready.append(target)
        if count != len(graph):
            return True
    return False


def unity_layout_snapshot(session):
    if session.interaction_state is not InteractionState.EMVR_DIRECT:
        return None
    raw = session.design_context.get('emvr_design', {})
    raw = raw if isinstance(raw, dict) else {}
    values = merge_emvr_structured_requirements(raw)
    brief = raw.get('authoritative_experiment_brief', {})
    brief = brief if isinstance(brief, dict) else {}
    cleared = set(_texts(raw.get('explicitly_cleared_fields')))

    def current(field, prior=None):
        if field in cleared:
            return []
        if field in values:
            return _texts(values[field])
        return _texts(brief.get(prior or field))

    objects = current('research_object', 'objects')
    if not objects and not brief and 'research_object' not in values and 'research_object' not in cleared:
        flow = raw.get('formula_flow', {})
        analysis = flow.get('topic_analysis', {}) if isinstance(flow, dict) else {}
        objects = _texts(analysis.get('mentioned_objects')) if isinstance(analysis, dict) else []
    # The canonical research_object is commonly the projection of this list.
    if len(objects) == 1:
        objects = list(dict.fromkeys(s.strip() for s in re.split(r'[、;；\n]', objects[0]) if s.strip()))
    operations = current('required_behaviors', 'operations')
    observations = current('observed_quantities')
    spatial_notes = current('room_spatial_requirements')
    placements = spatial_notes + current('object_constraints', 'boundary_conditions')
    sections = [
        {'label': label, 'items': rows} for label, rows in (
            ('实验对象', objects), ('操作', operations), ('观察内容', observations), ('位置说明', placements)
        ) if rows
    ]
    nodes = [{'id': f'object-{i}', 'label': name, 'kind': 'observation' if re.search(r'观察(?:位置|点|标记)|observation (?:point|marker)', name, re.I) else 'component'} for i, name in enumerate(objects)]
    links, unresolved, flows, notes = [], [], [], []
    clauses = [s.strip(' 。.') for row in placements + operations + observations
               for s in re.split(r'[；;。\n]', row) if s.strip(' 。.')]
    directions = '|'.join(RELATIONS)
    no_fixed_observer = any(re.fullmatch(r'(?:不需要|无需|不设置)固定观察(?:点|位置)|no fixed observation point is needed', c, re.I) for c in clauses)
    too_large = len(nodes) > 64 or len(clauses) > 256
    if too_large:
        notes.append('组件或位置说明较多，示意图暂时留白，请以文字说明为准。')
        clauses = []
    # Relation clauses must stand alone. Negative/conditional/suggested or
    # partially specified prose is not converted to affirmative geometry.
    for clause in clauses:
        subjects = [n for n in nodes[:len(objects)] if clause.casefold().startswith(n['label'].casefold())]
        flow = re.fullmatch(r'(.+?)(?:→|->|从而|使得)(.+)', clause)
        if flow and objects and any(name.casefold() in flow[1].casefold() for name in objects) and not re.search(r'不|未|建议|可以|如果|\b(?:not|could|should|if)\b', clause, re.I):
            flows.append({'operation':flow[1].strip(),'feedback':flow[2].strip()})
        for anchor in nodes[:len(objects)]:
            name = re.escape(anchor['label'])
            observer = re.fullmatch(rf'在\s*{name}\s*的?\s*(圆心|中心)\s*(?:处)?(?:观察|测量|读取)(.+)', clause)
            observer_en = re.fullmatch(rf'(?:observe|measure|read) .+ at the center of {name}', clause, re.I)
            if observer or observer_en:
                marker_id = 'observation-' + anchor['id']
                marker = next((n for n in nodes if n['id'] == marker_id), None)
                if marker is None:
                    marker = {'id':marker_id,'label':'观察位置','kind':'observation'}
                    nodes.append(marker)
                links.append({'source': marker['id'], 'target': anchor['id'], 'relation':'center', 'evidence':clause})
            for node in subjects:
                if node is anchor:
                    continue
                subject = re.escape(node['label'])
                match = re.fullmatch(rf'{subject}\s*(?:位于|放在|置于|在)\s*{name}\s*的?\s*({directions})(?:处)?', clause, re.I)
                english = re.fullmatch(rf'{subject}\s+is\s+(?:to the\s+)?(left|right|in front|behind|at the center)\s+of\s+{name}', clause, re.I)
                relation = RELATIONS[match[1].lower()] if match else None
                if english:
                    relation = {'left':'left','right':'right','in front':'front','behind':'back','at the center':'center'}[english[1].lower()]
                if relation:
                    links.append({'source':node['id'],'target':anchor['id'],'relation':relation,'evidence':clause})
    # Conflicting statements do not pick a winner. Leave that relation blank.
    by_pair = {}
    opposites = {'left':'right','right':'left','front':'back','back':'front','center':'center'}
    for link in links:
        pair = tuple(sorted((link['source'],link['target'])))
        direction = link['relation'] if pair[0] == link['source'] else opposites[link['relation']]
        by_pair.setdefault(pair, {})[direction] = link
    links = []
    for variants in by_pair.values():
        directions_present = set(variants)
        conflict = ({'left', 'right'} <= directions_present
                    or {'front', 'back'} <= directions_present
                    or 'center' in directions_present and len(variants) > 1)
        if conflict:
            unresolved.append('位置说明存在冲突，请明确应保留哪一种相对位置。')
        else:
            links.extend(variants.values())
    if _has_position_cycle(nodes, links):
        links = []
        unresolved.append('位置说明存在循环冲突，请核对相对位置。')
    observation_ids = {n['id'] for n in nodes if n['kind'] == 'observation'}
    if no_fixed_observer and any(r['source'] in observation_ids or r['target'] in observation_ids for r in links):
        links = [r for r in links if r['source'] not in observation_ids and r['target'] not in observation_ids]
        unresolved.append('观察说明存在冲突：请明确是否需要固定观察位置。')
    placed = {link[key] for link in links for key in ('source','target')}
    unknown_objects = [node['label'] for node in nodes if node['id'] not in placed]
    if not nodes:
        unresolved.append('实验使用哪些对象？请先明确实验对象。')
    elif unknown_objects and not spatial_notes and not too_large:
        unresolved.append('请明确这些对象的相对位置：' + '、'.join(unknown_objects) + '。')
    if unknown_objects and spatial_notes and not too_large:
        notes.append('已有位置说明未全部绘出，请以文字为准；无需重复确认已说明的位置。')
    if not no_fixed_observer and not any(n['kind'] == 'observation' and n['id'] in placed for n in nodes) and (not spatial_notes or not observations) and not too_large:
        unresolved.append('在哪里观察或读取反馈？请明确观察位置；若不需要固定观察点，也请说明。')
    return {'design_id':session.design_id, 'revision':session.revision,
            'summary':deepcopy(sections), 'nodes':nodes, 'relations':links,
            'flows':list({(f['operation'], f['feedback']): f for f in flows}.values()),
            'questions':list(dict.fromkeys(unresolved)), 'notes':notes}
