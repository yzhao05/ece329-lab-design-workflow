"""Local browser fixture only: real API/SQLite, deterministic model substitute.

Run with PYTHONPATH=src python -m tests.feedback_smoke_server --database <tempfile>.
Never use this fixture as a deployed service; its review token is public test data.
"""
import argparse
import json
import mimetypes
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from wsgiref.simple_server import make_server, WSGIRequestHandler

from ece329_workflow.api import WorkflowAPI
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.experience import ExperienceStore, FeedbackService, ModelExperienceExtractor
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.security import APISettings
from ece329_workflow.store import SQLiteSessionStore
from tests.test_feedback_pipeline import extraction_response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--final-review', action='store_true', help='Enable a completed Guided fixture')
    parser.add_argument('--models', action='store_true', help='Enable model selection with the test transport')
    parser.add_argument('--deepseek', action='store_true', help='Include the real DeepSeek protocol adapter with a fake HTTP service')
    parser.add_argument('--feedback-switch', action='store_true', help='Simulate invalid OpenAI feedback output and a working DeepSeek backup')
    parser.add_argument('--usage', action='store_true', help='Attach deterministic token usage for accounting UI checks')
    parser.add_argument('--rule-authoring', action='store_true', help='Deterministic controlled-rule revision fixture')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'docs'
    engine = WorkflowEngine(generator=RuleBasedStageGenerator(), store=SQLiteSessionStore(args.database))
    if args.models:
        from tests.test_model_selection import make_engine, ModelTransport
        engine, model_transport = make_engine(store=engine.store, transport=ModelTransport())
    if args.deepseek:
        from tests.test_deepseek_provider import mixed_engine
        mixed, model_transport, deepseek_chat = mixed_engine()
        mixed.store = engine.store
        engine = mixed
        args.models = True
    generator = SimpleNamespace(model='test-double', reasoning_effort='low',
                                transport=SimpleNamespace(create=extraction_response))
    feedback_requests = []
    if args.rule_authoring:
        from tests.test_feedback_pipeline import candidate
        from ece329_workflow.experience_rules import contract
        def revision_response(request):
            if request['text']['format']['name']=='feedback_rule_revision':
                return {'output_text':json.dumps({'candidate_json':json.dumps(candidate(execution=contract())), 'unsupported_actions':[]})}
            return extraction_response(request)
        generator=SimpleNamespace(model='deepseek-flash',allowed_models=('deepseek-flash',),
                                  transport=SimpleNamespace(create=revision_response))
    if args.feedback_switch:
        from ece329_workflow.provider_transport import ProviderResponsesTransport
        def feedback_response(provider, request):
            feedback_requests.append({'provider':provider,'model':request['model']})
            return {'output_text':'invalid JSON'} if provider == 'openai' else extraction_response(request)
        generator = SimpleNamespace(model='gpt-5.4-mini', reasoning_effort='high',
            allowed_models=('gpt-5.4-mini','deepseek-flash'), transport=ProviderResponsesTransport(
                openai=SimpleNamespace(create=lambda request: feedback_response('openai',request)),
                deepseek=SimpleNamespace(create=lambda request: feedback_response('deepseek',request))))
    if args.usage:
        def meter(transport):
            original = transport.create
            def create(body):
                response = original(body)
                response['usage'] = {'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':200}}
                return response
            transport.create = create
        if args.models:
            meter(engine.generator.transport)
        meter(generator.transport)
    # This fixture deliberately starts with failed OpenAI output to test an
    # explicit user switch. Normal production extraction defaults to DeepSeek.
    feedback_env = {'ECE329_FEEDBACK_MODEL': 'gpt-5.4-mini'} if args.feedback_switch else {}
    service = FeedbackService(ExperienceStore(args.database), ModelExperienceExtractor(generator, feedback_env))
    api = WorkflowAPI(engine, APISettings(allowed_origins=('*',), feedback_admin_token='smoke-maintainer', rate_limit_requests=500), feedback_service=service)
    if args.final_review:
        from ece329_workflow.localization import DisplayTranslator
        from ece329_workflow.openai_generator import OpenAIStageGenerator
        from tests.test_model_selection import ModelTransport
        api.translator = DisplayTranslator(OpenAIStageGenerator(transport=ModelTransport()))
    def app(environ, start_response):
        path = unquote(environ['PATH_INFO'])
        if args.final_review and path == '/__smoke/emvr-complete':
            import hashlib
            from tools.export_emvr37_review import reviewed_engine
            _, session = reviewed_engine()
            session.access_token_hash = hashlib.sha256(b'smoke-emvr-pdf').hexdigest()
            engine.store.save(session)
            result = engine.get_design(session.design_id)
            result['design_access_token'] = 'smoke-emvr-pdf'
            start_response('200 OK', [('Content-Type', 'application/json')])
            return [json.dumps(result).encode()]
        if args.final_review and path == '/__smoke/guided-complete':
            from ece329_workflow.models import WorkflowStatus, Stage
            created = engine.create_design('比较电流大小和磁场强度', interaction_state='GUIDED_DESIGN')
            session = engine.store.get(created['design_id'])
            session.status = WorkflowStatus.COMPLETE
            session.current_stage_index = len(Stage) - 1
            session.design_context['synthesis'] = {'student_summary': '我比较电流变化与磁场强度之间的关系。'}
            engine.store.save(session)
            result = engine.get_design(session.design_id)
            result.update({key: created[key] for key in ('design_access_token', 'design_resume_token')})
            start_response('200 OK', [('Content-Type', 'application/json')])
            return [json.dumps(result).encode()]
        if args.feedback_switch and path == '/__smoke/feedback-requests':
            start_response('200 OK', [('Content-Type', 'application/json')])
            return [json.dumps(feedback_requests).encode()]
        if args.models and path == '/__smoke/model-requests':
            start_response('200 OK', [('Content-Type', 'application/json')])
            records = [{'model': r['model'], 'schema': r['text']['format']['name'],
                                'reasoning_effort': r.get('reasoning', {}).get('effort'),
                                'max_output_tokens': r.get('max_output_tokens'),
                                'previous_response_id': r.get('previous_response_id')}
                               for r in model_transport.requests]
            if args.deepseek:
                records += [{'model': r['model'], 'provider':'deepseek', 'thinking':r['thinking']['type'],
                             'reasoning_effort':r.get('reasoning_effort'), 'max_output_tokens':r.get('max_tokens')} for r in deepseek_chat.requests]
            return [json.dumps(records).encode()]
        if path.startswith('/v1/') or path in {'/health', '/ready'}:
            return api(environ, start_response)
        target = (root / (path.lstrip('/') or 'index.html')).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Not found']
        data = target.read_bytes()
        if target.name == 'config.js':
            data = f'window.ECE329_CONFIG={{API_BASE_URL:"{url}",REQUEST_TIMEOUT_MS:10000}};'.encode()
        elif target.suffix == '.html':
            # Test-only ephemeral loopback port, without changing production CSP.
            data = data.replace(b'http://127.0.0.1:8080', url.encode())
        start_response('200 OK', [('Content-Type', (mimetypes.guess_type(target.name)[0] or 'application/octet-stream') + '; charset=utf-8'), ('Cache-Control', 'no-store')])
        return [data]
    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *_):
            pass
    with make_server('127.0.0.1', 0, app, handler_class=QuietHandler) as server:
        url = f'http://127.0.0.1:{server.server_port}'
        print(json.dumps({'url': url, 'pid': os.getpid()}), flush=True)
        try:
            server.serve_forever()
        finally:
            service.stop()


if __name__ == '__main__':
    main()
