# ECE329 Lab Studio web frontend

This folder is a dependency-free static site intended for GitHub Pages. Its entry point is `index.html`.

## Current runtime modes

- When `assets/config.js` has an empty `API_BASE_URL`, the site runs in clearly labelled demo mode. Demo responses are generated locally and are not presented as Agent output.
- When `API_BASE_URL` points to a compatible workflow backend, the site uses `/health`, `/v1/designs`, and `/v1/designs/{design_id}/turns`.

Never put an OpenAI key, database password, or other secret in this directory. Every file published by GitHub Pages can be read by site visitors. The model API must be called from the separately hosted backend.

If the backend enables `ECE329_ACCESS_CODE`, the page asks the student for that course code on first use. It keeps the code and short-lived per-design bearer token in `sessionStorage`. A separate rotating resume credential is persisted only so the same design can be reopened after the tab closes; the backend exchanges it for new credentials and invalidates the old pair. None of these values belong in source files, Git commits, screenshots, or shared logs.

Each mutation request also sends a stable `Idempotency-Key`/`turn_id`, so retrying the same timed-out request does not repeat a model call or advance the workflow twice.

## Preview locally

From the repository root, use CMD:

```bat
python -m http.server 4173 --directory docs
```

Then open `http://127.0.0.1:4173`.

## Publish with GitHub Actions

The repository includes `.github/workflows/pages.yml` and publishes the `docs` folder.

1. Push the repository to GitHub using `main` as the default branch.
2. Open **Settings → Pages** in the repository.
3. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Push a change under `docs/`, or run the workflow manually from the **Actions** tab.
5. Read the published URL from the deployment summary.

## Connect the deployed backend

Recommended: do not edit `assets/config.js`. In the GitHub repository, open **Settings → Secrets and variables → Actions → Variables**, create `ECE329_API_BASE_URL` with the public backend HTTPS URL, and run the Pages workflow. The workflow changes only the uploaded artifact; the committed config remains blank.

For a manual-only deployment, edit `assets/config.js`:

```js
window.ECE329_CONFIG = Object.freeze({
  API_BASE_URL: "https://your-workflow-api.example.edu",
  REQUEST_TIMEOUT_MS: 70000,
});
```

The backend must use HTTPS when the page uses HTTPS, list the GitHub Pages origin in `ECE329_ALLOWED_ORIGINS`, and implement the existing workflow API contract. Full container and acceptance steps are in `../DEPLOYMENT.md`.
