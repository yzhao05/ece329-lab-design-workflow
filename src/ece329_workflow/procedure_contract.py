"""Lossless procedure parsing and read-only lookup of persisted steps."""
import json
import re

from .models import Stage


def decode_text_list(value):
    """Unwrap a JSON list serialized inside a scalar or a list entry."""
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
    return value


def has_positive_action(text: str, pattern: str) -> bool:
    """Keep negation local; a later affirmative command is a separate action."""
    for match in re.finditer(pattern, text, re.I):
        if re.match(r"\s*(?:按钮)?\s*(?:已|保持|仍|应)?\s*(?:禁用|隐藏|不可用|不能使用)", text[match.end():]):
            continue
        prefix = re.split(r"[。；;，,\n]|但是|但|而是|然后|随后", text[:match.start()])[-1]
        if re.search(r"不要|无需|禁止|禁用|避免|不得|不能|不应|勿|\b(?:do not|don't|never)\b", prefix, re.I):
            continue
        if re.search(r"不(?:自动|再|执行|触发|进行|点击|调用|使用|创建)?\s*(?:$|(?:Capture|Restore|Reset|Back)[^。；;，,\n]*$)", prefix, re.I):
            continue
        return True
    return False


def procedure_steps(value):
    value = decode_text_list(value)
    if isinstance(value, list):
        if len(value) == 1:
            return procedure_steps(value[0])
        return [str(x).strip() for x in value if isinstance(x, str) and x.strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    text = value.strip().strip('*')
    markers = list(re.finditer(r"(?:^|(?<=[\s。；;：:]))(?:S(?P<s>\d+)\s*|(?P<n>\d+)[，,、）)]\s*|(?P<dot>\d+)\.(?!\d)\s*)", text))
    numbers = [int(m.group('s') or m.group('n') or m.group('dot')) for m in markers]
    if len(markers) > 1 and numbers == list(range(1, len(markers) + 1)):
        return [text[m.end():markers[i+1].start() if i+1<len(markers) else len(text)].strip(' \n；;')
                for i,m in enumerate(markers)]
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) > 1:
        return lines
    # Chinese sentence boundaries cannot split decimals, units or ranges.
    return [x.strip() for x in re.split(r"(?<=。)\s*", text) if x.strip()]


def current_procedure(session):
    emvr = session.design_context.get('emvr_design', {})
    state = session.design_context.get('stage_design_state', {})
    if any('procedure_steps' in x.get('explicitly_cleared_fields', []) for x in (emvr,state)):
        return []
    fields = emvr.get('field_state', {})
    if 'procedure_steps' in fields:
        return procedure_steps(fields['procedure_steps'])
    if 'procedure_steps' in state:
        return procedure_steps(state['procedure_steps'])
    return procedure_steps(session.stage_outputs.get(Stage.CONCEPTUAL_PROCEDURE.value, {}).get('stage_payload', {}).get('procedure_steps'))


def historical_procedure(session):
    """Return the latest complete *displayed* procedure; never silently restore it."""
    start = session.model_context.get('design_history_start', 0)
    start = start if isinstance(start, int) and start >= 0 else 0
    for entry in reversed(session.history[start:]):
        if entry.get('resolved_intent', {}).get('intent') == 'NEW_TOPIC':
            break
        steps = procedure_steps(entry.get('output', {}).get('stage_payload', {}).get('procedure_steps'))
        if len(steps) >= 5:
            return steps
    return []
