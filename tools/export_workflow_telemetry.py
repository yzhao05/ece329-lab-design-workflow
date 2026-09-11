"""Read-only research export, with grouped comparisons and unknown-usage counts."""
import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def summarize(records):
    groups = defaultdict(list)
    for row in records:
        config = row.get('model_config', {})
        key = (row.get('mode'), row.get('initial_stage'), config.get('strategy'),
               config.get('experience_enabled', True), config.get('adaptive_enabled', False))
        groups[key].append(row)
    result = []
    for (mode, stage, strategy, experience, adaptive), rows in groups.items():
        checks = [s['validator_pass'] for row in rows for s in row.get('stages', []) if type(s.get('validator_pass')) is bool]
        result.append({'mode':mode,'stage':stage,'strategy':strategy,'experience_enabled':experience,'adaptive_enabled':adaptive,
                       'turns':len(rows),'failed_turns':sum(r.get('status')=='failed' for r in rows),
                       'mean_latency_ms':sum(r['latency_ms'] for r in rows)/len(rows),
                       'validator_pass_rate':sum(checks)/len(checks) if checks else None,
                       'known_input_tokens':sum(r['input_tokens'] for r in rows if r.get('input_tokens') is not None),
                       'known_output_tokens':sum(r['output_tokens'] for r in rows if r.get('output_tokens') is not None),
                       'unknown_usage_turns':sum(r.get('input_tokens') is None or r.get('output_tokens') is None for r in rows)})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--design-id')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    path=args.database.resolve(strict=True)
    if args.output.resolve()==path:
        parser.error('Output must not overwrite the database')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        records=[json.loads(row[0]) for row in db.execute(
            'SELECT record FROM workflow_telemetry WHERE (? IS NULL OR design_id=?) ORDER BY created,id',(args.design_id,args.design_id))]
        feedback=defaultdict(list)
        for ticket_id,raw,status in db.execute('SELECT id,payload,status FROM feedback_tickets WHERE (? IS NULL OR design_id=?)',(args.design_id,args.design_id)):
            payload=json.loads(raw)
            if payload.get('telemetry_id'):
                feedback[payload['telemetry_id']].append({'id':ticket_id,'category':payload['category'],'status':status})
    for row in records:
        row['user_feedback']=feedback[row['id']]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'records':records,'summary':summarize(records)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Exported {len(records)} turns; token usage is unknown when the provider did not report it.')


if __name__=='__main__':
    main()
