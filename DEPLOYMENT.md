# Production deployment

The static frontend and model backend must be deployed separately:

- GitHub Pages publishes `docs/`.
- An HTTPS container service runs the Python WSGI API.
- `OPENAI_API_KEY` exists only in the backend service environment.

## 1. Test the production container locally

From CMD in the project root:

```bat
copy .env.example .env.production
notepad .env.production
docker build -t ece329-workflow-api .
docker run --rm --name ece329-api -p 8080:8080 --env-file .env.production -v ece329-data:/data ece329-workflow-api
```

For a local frontend preview, set this value in `.env.production`:

```text
ECE329_ALLOWED_ORIGINS=http://127.0.0.1:4173
```

Check `http://127.0.0.1:8080/ready`. A production-ready local response should show `status=ready`, `generator.provider=openai`, `storage.provider=sqlite`, and `storage.read_write_check=ok`. The process can report that a host volume is required, but only a restart test can prove that the platform mounted one.

## 2. Deploy the backend container

Use any container host that provides a public HTTPS URL and a persistent disk. Configure:

```text
OPENAI_API_KEY=<backend secret>
ECE329_GENERATOR=auto
OPENAI_MODEL=gpt-5.4-mini
OPENAI_REASONING_EFFORT=medium
OPENAI_INTENT_MAX_OUTPUT_TOKENS=1400
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_OUTPUT_TOKENS=2400
OPENAI_STAGE_ONE_MAX_OUTPUT_TOKENS=3200
OPENAI_FINAL_MAX_OUTPUT_TOKENS=5000
ECE329_OPENAI_FALLBACK=true
ECE329_OPENAI_STATEFUL=true
ECE329_ACCESS_CODE=<generate-a-long-random-course-code>
ECE329_ALLOWED_ORIGINS=https://YOUR_GITHUB_USERNAME.github.io
ECE329_TRUST_PROXY=false
ECE329_DATABASE_PATH=/data/ece329.sqlite3
ECE329_RATE_LIMIT_REQUESTS=30
ECE329_RATE_LIMIT_WINDOW_SECONDS=60
ECE329_MAX_BODY_BYTES=65536
ECE329_MAX_TEXT_CHARS=4000
ECE329_SESSION_TTL_DAYS=30
ECE329_ENABLE_PROMPT_DEBUG=false
```

This block contains every environment variable used by the normal production
path. `ECE329_PROMPT_DEBUG_TOKEN` is intentionally absent: add it only when
temporarily setting `ECE329_ENABLE_PROMPT_DEBUG=true`, and remove it again when
debugging ends. Render supplies `PORT` automatically, so do not add a manual
`PORT` value unless you deliberately want to override Render's default.

Mount a persistent volume at `/data`. Do not put a repository path in `ECE329_ALLOWED_ORIGINS`: for `https://name.github.io/repository/`, the browser origin is only `https://name.github.io`.

For this student-facing workflow, set `ECE329_OPENAI_STATEFUL=true` so formal replies continue through `previous_response_id`. The returned response ID is stored inside that one design session and is never shared across students; intent classification still uses `store=false` without a response chain. The backend resends the current system instructions on every formal reply, and the local `design_state` remains authoritative. Use `false` only when your privacy policy requires fully local replay with `store=false`.

Set `ECE329_TRUST_PROXY=true` only when the hosting platform overwrites and validates `X-Forwarded-For`. Otherwise leave it `false` so clients cannot forge the address used by the limiter.

After deployment, verify both liveness and readiness:

```bat
curl https://YOUR-BACKEND-HOST/health
curl https://YOUR-BACKEND-HOST/ready
```

After several real conversations, inspect `generator` in `/health`. A growing
`api_failures` value points to transport, timeout, authentication, quota, or
HTTP failures. A growing `output_rejections` value means the model answered but
did not satisfy the workflow contract; `repair_successes` counts cases fixed by
the automatic one-time retry. `fallback_calls` and `last_fallback_reason` show
whether students are currently receiving the course-built-in fallback. These
fields contain no API key or student message text.

## 3. Connect GitHub Pages without editing config.js

In the GitHub repository:

1. Open **Settings → Secrets and variables → Actions → Variables**.
2. Create `ECE329_API_BASE_URL` with the backend HTTPS origin, for example `https://ece329-api.example.com`.
3. Open **Actions → Deploy static site to GitHub Pages → Run workflow**.

The workflow injects this public URL only into the uploaded Pages artifact. The committed `docs/assets/config.js` remains blank, so local development still starts in demo mode and no repository file is rewritten.

## 4. Acceptance checks

- The webpage badge says `课程服务已连接`.
- Browser developer tools show `/ready` and `/v1/designs` requests going to the HTTPS backend.
- `/health` reports the expected model, `storage.provider=sqlite`, and `storage.host_volume_required=true`.
- Creating a design without `X-ECE329-Access-Code` returns HTTP 401 when the access code is configured.
- The returned one-time `design_access_token` can continue that design; omitting it from design routes returns HTTP 401.
- Requests from an origin not listed in `ECE329_ALLOWED_ORIGINS` return HTTP 403.
- Repeated POST requests beyond the configured window return HTTP 429.
- Restarting the container with the same `/data` volume preserves an existing `design_id`.
- Clicking “新建设计” sends `DELETE /v1/designs/{design_id}` before clearing the browser session.

Inactive sessions expire after `ECE329_SESSION_TTL_DAYS` (default 30). Keep
`ECE329_ENABLE_PROMPT_DEBUG=false` in production: the prompt-packet endpoint contains
internal workflow instructions. If private debugging temporarily requires it, also set
a separate strong `ECE329_PROMPT_DEBUG_TOKEN` and send it in `X-ECE329-Debug-Token`.

## Current scaling boundary

The included SQLite store and in-process limiter are intended for one service instance. The Docker command intentionally runs one Gunicorn worker with multiple threads. Before scaling to multiple containers, replace SQLite with a shared database and the limiter with a shared service such as a gateway or Redis-backed limiter.

The shared course code is admission control for a small class, not full user authentication. For an unrestricted public launch, put school SSO, an API gateway, or a bot challenge in front of `POST /v1/designs` and enforce an OpenAI project budget.
