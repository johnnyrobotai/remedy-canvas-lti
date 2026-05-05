# Remedy Canvas LTI

Canvas LMS LTI 1.3 tool that scans entire courses for WCAG 2.2 AA accessibility
issues and remediates them in-place — no export/import.

The backend is FastAPI (Python 3.12+) and the frontend is React + TypeScript +
Vite. Persistent state lives in Postgres with the `pgvector` extension. Cloud
AI features (alt text, vision-assisted remediation) call Ollama Cloud through
the OpenAI-compatible client (`OLLAMA_BASE_URL=https://ollama.com/v1`,
`OLLAMA_MODEL=kimi-k2.6:cloud`). There is no active Gemini, Vertex, or
OpenRouter provider in the code.

See `CLAUDE.md` for architecture and dev patterns and `PRD.md` for the rule
inventory and phased plan.

## Layout

- `lti_app/` — FastAPI backend (`lti_app.main:app`), LTI 1.3 routes, Canvas
  client, AI client, accessibility/remediation engines, Postgres repositories.
- `frontend/` — React SPA, Vite dev server, package name
  `remedy-canvas-lti-frontend`.
- `docs/production-deployment.md` — deployment checklist and Canvas Developer
  Key URLs.

## Quickstart

Copy `.env.example` to `.env` and fill in values as needed.

```bash
# Backend (from this directory)
pip install -e ".[dev]"
AUTH_BYPASS_FOR_LOCAL=true uvicorn lti_app.main:app --reload --port 8001

# Frontend (in a second terminal)
cd frontend && npm install && npm run dev
```

The Vite dev server runs on `:5173` and proxies `/api` to the backend on
`:8001`. With `AUTH_BYPASS_FOR_LOCAL=true` the app skips LTI session
enforcement so you can iterate without launching from Canvas.

### Docker Compose

`docker-compose.yml` brings up Postgres (pgvector) plus the app. Use the `dev`
service for live-reload backend with the source mounted, or `app` for the
production-style image.

```bash
docker-compose up --build dev
```

## Environment Variables

`.env.example` is the source of truth. Common groups:

- App: `ENVIRONMENT`, `AUTH_BYPASS_FOR_LOCAL`, `APP_BASE_URL`,
  `ALLOWED_HOSTS`, `CORS_ORIGINS`, `SESSION_SECRET_KEY`.
- Database: `DATABASE_URL` (Postgres with `pgvector`), or the
  `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` trio used by
  `docker-compose.yml`.
- AI (Ollama Cloud): `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL`.
- LTI / Canvas: `LTI_TOOL_PRIVATE_KEY_PEM` (or
  `LTI_TOOL_PRIVATE_KEY_PEM_FILE`), `CANVAS_OAUTH2_CLIENT_ID`,
  `CANVAS_OAUTH2_CLIENT_SECRET`, `CANVAS_WEBHOOK_SECRET`.
- Local Canvas dev: `LOCAL_CANVAS_BASE_URL`, `LOCAL_CANVAS_CLIENT_ID`,
  `LOCAL_CANVAS_DEPLOYMENT_ID`, `LOCAL_CANVAS_API_TOKEN`.

`lti_app/config.py` enforces production guards: `AUTH_BYPASS_FOR_LOCAL` must be
false, `SESSION_SECRET_KEY` and `DATABASE_URL` must be set, `APP_BASE_URL`
must be `https://`, `CORS_ORIGINS` cannot contain `*`, and `OLLAMA_API_KEY`
is required when `OLLAMA_BASE_URL` points at `https://ollama.com/v1`.

## Production

The production container builds the React SPA and serves both the API and the
built assets from a single FastAPI process. It listens on `$PORT` (defaults to
`8080`) for Cloud Run or a reverse proxy.

Required production settings:

```bash
ENVIRONMENT=production
AUTH_BYPASS_FOR_LOCAL=false
APP_BASE_URL=https://your-lti-host.example.edu
ALLOWED_HOSTS=your-lti-host.example.edu
CORS_ORIGINS=https://your-lti-host.example.edu
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB
SESSION_SECRET_KEY=<32+ bytes of random secret>
OLLAMA_BASE_URL=https://ollama.com/v1
OLLAMA_API_KEY=<secret>
OLLAMA_MODEL=kimi-k2.6:cloud
```

LTI platform registrations are read from Postgres at startup; seed at least
one before Canvas traffic reaches the tool, or `/lti/jwks` will return an
empty key set and Canvas launches will fail validation.

See [docs/production-deployment.md](docs/production-deployment.md) for the
full deployment checklist, Canvas Developer Key URLs, and post-deploy
verification.

## License

MIT. See `LICENSE`.
