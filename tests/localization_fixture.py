"""Translation test double: exercises transport, not translation quality."""
import json
from pathlib import Path
import re


def translations(text, english=True):
    catalogue = (Path(__file__).parents[1] / 'docs/assets/i18n-catalog.js').read_text(encoding='utf-8')
    pairs = json.loads(catalogue[catalogue.index('{'):].rstrip().removesuffix(';'))
    rows = json.loads(text)
    output = []
    for row in rows:
        result = row['text']
        if english:
            for source, target in sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True):
                result = result.replace(source, target)
            result = re.sub(r'[\u3400-\u9fff]+', 'sample', result)
        else:
            result = '实验说明：' + result
        output.append({'id': row['id'], 'text': result})
    return {'translations': output}
