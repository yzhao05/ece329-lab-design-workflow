"""Surgical updates of explicit numerical assignments, preserving other inputs.

Only named scalar assignments are replaced. Geometry, coordinate formulas and
unrecognized prose are retained; complete replacements remain explicit actions.
"""
import re

from .numerical_contract import NUMBER, LENGTH_UNIT

_ASSIGN = r'\s*(?:[:：=]|改为|改成|设为|设置为|为|取)?\s*'
_RULES = (
    ('source_length', rf'(?:源路径|源线段|电流元|源段|导线)(?:的)?(?:段长|离散长度|长度步长){_ASSIGN}(?P<value>{NUMBER}\s*{LENGTH_UNIT})'),
    ('line_step', rf'(?<!段)(?:场线(?:积分)?|RK4)?步长{_ASSIGN}(?P<value>{NUMBER}\s*{LENGTH_UNIT})'),
    ('steps', rf'(?:最大步数|步数上限){_ASSIGN}(?P<value>{NUMBER})(?:\s*步)?'),
    ('steps', rf'(?:每条)?(?:最大|最多){_ASSIGN}(?P<value>{NUMBER})\s*步'),
    ('source_count', rf'(?P<shape>直导线|圆环|螺线管)[^\d。；;\n]{{0,12}}?(?P<value>{NUMBER})\s*段'),
    ('tolerance', rf'(?P<kind>相对误差|绝对误差|位置偏差|收敛容差|误差容差|容差|误差){_ASSIGN}(?P<value>{NUMBER}(?:\s*(?:%|T|{LENGTH_UNIT}))?)'),
)


def _assignments(text):
    matches = []
    occupied = []
    for kind, pattern in _RULES:
        for match in re.finditer(pattern, text, re.I):
            start, end = match.span('value')
            if any(start < right and end > left for left, right in occupied):
                continue
            # A refinement is a separate input, not an older copy of the
            # primary step/count. Never replace both with one correction.
            clause = re.split(r'[。；;\n]', text[:match.start()])[-1]
            refinement = bool(re.search(r'复算|验收|减半|精度检查', clause))
            detail = match.groupdict().get('shape') or match.groupdict().get('kind') or ''
            if detail in {'收敛容差', '误差容差', '容差', '误差'}:
                detail = 'error'
            matches.append(((kind, detail, refinement), start, end, match.group('value'), match.span()))
            occupied.append((start, end))
    return sorted(matches, key=lambda item: item[1])


def merge_numerical_contract(previous: str, addition: str) -> str:
    if not previous:
        return addition
    incoming = _assignments(addition)
    latest = {key: value for key, _, _, value, _ in incoming}
    prior = _assignments(previous)
    existing = {item[0] for item in prior}
    result = previous
    for key, start, end, _, _ in reversed(prior):
        if key in latest:
            result = result[:start] + latest[key] + result[end:]
    # A plain assignment need not be appended a second time. Preserve any
    # accompanying explanation and every newly supplied component verbatim.
    residual = addition
    for _, _, _, _, (start, end) in reversed(incoming):
        residual = residual[:start] + residual[end:]
    residual = re.sub(r'补充|修正|更正|修改|更新|分段数|求积规则|[\s：:，,。；;、]', '', residual)
    if incoming and not residual and all(key in existing for key in latest):
        return result
    if addition.replace(' ', '') in result.replace(' ', ''):
        return result
    return result.rstrip('；') + '；补充：' + addition
