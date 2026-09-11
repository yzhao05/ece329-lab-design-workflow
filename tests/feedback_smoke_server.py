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
from tests.test_feedback_pipeline import candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--models', action='store_true', help='Enable model selection with the test transport')
    parser.add_argument('--deepseek', action='store_true', help='Include the real DeepSeek protocol adapter with a fake HTTP service')
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
                                transport=SimpleNamespace(create=lambda _: {'output_text': json.dumps(candidate())}))
    service = FeedbackService(ExperienceStore(args.database), ModelExperienceExtractor(generator))
    api = WorkflowAPI(engine, APISettings(allowed_origins=('*',), feedback_admin_token='smoke-maintainer', rate_limit_requests=500), feedback_service=service)
    def app(environ, start_response):
        path = unquote(environ['PATH_INFO'])
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
