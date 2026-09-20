from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from tools.trigger_render_deploy import main


def test_unconfigured_hook_warns_without_contacting_any_service(capsys):
    assert main({}, lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))) == 0
    assert 'Publishing Pages alone does not migrate backend data' in capsys.readouterr().out


def test_hook_uses_verified_https_endpoint_exact_commit_and_bounded_timeout(capsys):
    seen = []
    class Response:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def send(request, timeout):
        seen.append((request, timeout))
        return Response()
    env = {'RENDER_DEPLOY_HOOK_URL': 'https://api.render.com/deploy/srv-example?key=test-secret', 'GITHUB_SHA': 'a' * 40}
    assert main(env, send) == 0
    request, timeout = seen[0]
    assert timeout == 30 and request.method == 'POST'
    assert parse_qs(urlsplit(request.full_url).query)['ref'] == ['a' * 40]
    assert 'test-secret' not in capsys.readouterr().out
    assert main(env, lambda *a, **k: (_ for _ in ()).throw(URLError('test-secret'))) == 1
    assert 'test-secret' not in capsys.readouterr().out
    env['RENDER_DEPLOY_HOOK_URL'] = 'https://not-render.example/deploy/srv-example?key=test-secret'
    assert main(env, send) == 1
    assert len(seen) == 1
