"""CI-only deploy trigger. Credentials are never printed or sent to the app."""
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(env=None, open_url=None):
    env = os.environ if env is None else env
    hook = env.get('RENDER_DEPLOY_HOOK_URL', '').strip()
    if not hook:
        print('::warning::RENDER_DEPLOY_HOOK_URL is not configured. Enable backend auto-deploy or add this repository secret. Publishing Pages alone does not migrate backend data.')
        return 0
    try:
        url = urlsplit(hook)
        query = dict(parse_qsl(url.query))
        sha = env.get('GITHUB_SHA', '')
        valid = (url.scheme == 'https' and url.netloc == 'api.render.com'
                 and re.fullmatch(r'/deploy/srv-[A-Za-z0-9]+', url.path)
                 and query.get('key') and not url.fragment and re.fullmatch(r'[a-fA-F0-9]{40}', sha))
        if not valid:
            print('::error::Invalid Render deploy hook configuration or commit SHA.')
            return 1
        query['ref'] = sha
        request = Request(urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), '')), method='POST')
        open_url = open_url or build_opener(NoRedirect()).open
        with open_url(request, timeout=30) as response:
            if response.status not in (200, 202):
                print('::error::Render did not accept the deployment request.')
                return 1
        print('Render deployment accepted. The new backend runs evidence migration at startup against its persistent database; this is not a deployment-completion check.')
        return 0
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        # Upstream exceptions can include the secret URL. Never print them.
        print('::error::Unable to trigger Render deployment. Check the deploy hook secret and Render service status.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
