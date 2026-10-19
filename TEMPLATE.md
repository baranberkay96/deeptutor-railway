# DeepTutor v1 Personal AI Tutor: Railway template spec

## Listing

| Field | Value |
|---|---|
| Name | **DeepTutor v1 Personal AI Tutor** (upstream `__version__ = "1.6.15"`) |
| Description | Personalized AI tutoring over your own documents, with quizzes and notes. (73 chars) |
| Category | AI/ML |
| Tags | deeptutor, ai-tutor, education, learning, rag, knowledge-base, quiz, llm, self-hosted |
| Icon | `https://cdn.jsdelivr.net/gh/HKUDS/DeepTutor@v1.6.15/web/public/logo.png` (200 OK, PNG 575×567, the app's own logo; no dashboard-icons entry exists) |

The description holds for v1.6.15: "your own documents" = knowledge bases (upload, parse, embed, cited retrieval), "quizzes" = the Quiz capability (`deep_question`) with an AI judge, "notes" = notebooks. Knowledge bases need an embedding model; the template seeds one when an OpenAI key (or `EMBEDDING_API_KEY`) is given, otherwise the user adds one in Settings.

## Base market template

One market template exists: **`deeptutor-personalized-ai-tutoring`** (2026-08-31, 1 active project). It is the base: one service from the official image with a volume at `/app/data`, and a base64-encoded Python preflight in the start command that writes `auth.json` / `system.json` / `model_catalog.json` before `/app/entrypoint.sh`. That approach is right, because the image ignores environment overrides. Problems:

- `ghcr.io/hkuds/deeptutor:latest` (unpinned).
- Admin password, OpenAI key and model are required user inputs; the password is typed by the deployer, not generated.
- Only OpenAI; no embedding model, so knowledge bases do not work until configured by hand.
- The OpenAI profile is forced active on every boot, overriding a provider chosen in Settings.
- Healthcheck `/login` only proves Next.js is up, not the API.
- No Redis (upstream compose ships it), one API worker, `ON_FAILURE` restarts.
- Patches `/etc/supervisor/supervisord.conf` at runtime and forces `RAILWAY_RUN_UID=0` (not needed: the image runs as root); `PORT` hard-wired to 3782.
- Secrets stay in the environment of every app process; the script is unreadable in the dashboard.

What it did well and is kept: API on loopback only, `cookie_secure`, CORS pinned to the public origin, code execution off.

## Pinned versions

| Service | Image / source | Version | Released | Checked |
|---|---|---|---|---|
| DeepTutor | `services/deeptutor/Dockerfile`: `FROM ghcr.io/hkuds/deeptutor:1.6.15@sha256:602d5dfbac7430e364069ac6672642cb0d764e046d189bf596d9ddc6b775c535` | v1.6.15 (tag commit `a31d826`, image created 2026-10-18T20:18Z, amd64 + arm64) | 2026-10-18 | 2026-10-20 |
| Redis | `redis:8.2` (as `_railway-base/redis.json`) | 8.2 | tag updated 2026-10-06 | 2026-10-20 |

Upstream publishes `X.Y.Z` (release tag without `v`) and `latest`; on 2026-10-20 both point to the same digest. The digest is pinned because `latest` and re-pushes would otherwise change what a redeploy builds. Upgrade: `docker buildx imagetools inspect ghcr.io/hkuds/deeptutor:<new>` (or the registry API), put tag and digest into the Dockerfile, back up the volume, redeploy. Read the release notes' "Upgrade Notes" first.

## Architecture

```
Browser ── HTTPS ──► DeepTutor service (public domain → port 3782)
                      supervisord (started by the image's /app/entrypoint.sh)
                      ├── Next.js web app   0.0.0.0:3782   /api/*, /ws/*, /files/* rewritten to ↓
                      ├── FastAPI backend   127.0.0.1:8001 (uvicorn, 2 workers)
                      └── railway_fatal_exit event listener
                      volume /app/data: settings JSON, accounts, JWT secret, knowledge bases,
                                         notebooks, sessions (SQLite), memory, logs, pip cache
                        │
                        └── redis://redis.railway.internal:6379  Redis (private, AOF, volume /data)
                              turn leases, event streams, background commands, leader election
```

## Services

### DeepTutor

| Setting | Value |
|---|---|
| Source | repo `baranberkay96/deeptutor-railway`, root `/services/deeptutor` (thin Dockerfile on the official image) |
| Start | image ENTRYPOINT `/opt/railway/entrypoint.sh` → `configure.py` → upstream `/app/entrypoint.sh` → supervisord |
| Healthcheck | `/api/auth/status`, timeout 900 s. The request goes through the Next.js proxy to the API, so 200 means both processes are up and the API's lifespan has finished (uvicorn only serves after startup). Covers the Redis wait (≤ 300 s), volume `chown`, optional extras installs and app import. |
| Public domain | yes, port 3782; `PORT=3782` |
| Volume | `/app/data` (upstream: "everything DeepTutor persists lives under ./data") |
| Restart | `ALWAYS` (no retry limit). supervisord exits with status 0 after the FATAL listener stops it, so `ON_FAILURE` would not restart it. |
| Replicas | 1 (one volume, SQLite) |

**Start sequence** (`railway/entrypoint.sh`, every boot):

1. `rmdir /app/data/lost+found` if empty.
2. `configure.py` (as root, from `/app`), fail-fast on bad input (exit 1 with a clear message, then Railway restarts):
   - Validates `PORT` (not 8001), `DEEPTUTOR_ADMIN_USERNAME` (upstream's username/email rule), `DEEPTUTOR_ADMIN_PASSWORD` (12–72 bytes), worker count (1–8), Redis URL scheme, URLs, booleans.
   - Redis: if `DEEPTUTOR_REDIS_URL` is set, `PING`s it every 5 s for up to `DEEPTUTOR_REDIS_WAIT_SECONDS` (300). DeepTutor's backend refuses to start when the configured coordinator is unreachable (`ApplicationContainer.start`), and refuses more than one worker without Redis (`CoordinationSettings.validate`). If Redis does not answer in time, the script starts 1 worker with in-memory coordination and logs it, instead of crash-looping.
   - `system.json`: `backend_port 8001`, `backend_workers`, `frontend_port $PORT`, `next_public_api_base` and `next_public_api_base_external` empty (the web app proxies to `http://127.0.0.1:8001`), `cors_origin` empty, `cors_origins` from `DEEPTUTOR_CORS_ORIGINS`, `sandbox_allow_subprocess` from `DEEPTUTOR_ALLOW_CODE_EXEC`; drops the legacy `public_api_base` alias.
   - `auth.json`: `enabled true`, `username`, bcrypt `password_hash` (kept when it still matches, so no rewrite on normal boots), `token_expire_hours`, `cookie_secure true`.
   - `integrations.json`: `turn_coordination.backend redis|memory` + `redis_url`; `pocketbase_url` forced empty.
   - `model_catalog.json` (only when a key is set): upserts profile `railway-llm` (and `railway-embedding`) with binding, base URL, key and one model entry `railway-llm-<model-slug>` (embedding: plus the known dimension for OpenAI v3 models). It is made active only when no valid profile is active or when it already is; a profile the user selects in Settings is never replaced. A changed `LLM_MODEL` adds a model entry and switches to it; a changed binding replaces the model list. Old embedding model entries are kept, so existing knowledge bases stay bound to their model.
   - Pre-creates `data/system/auth/auth_secret` through DeepTutor's own `load_or_create_auth_secret()`, so two workers do not race to create different JWT secrets on the first boot.
   - All other keys and files are left alone. Files are written atomically with mode 0600; invalid JSON is moved aside as `<name>.json.invalid-<UTC time>`.
3. `unset DEEPTUTOR_ADMIN_PASSWORD LLM_API_KEY EMBEDDING_API_KEY DEEPTUTOR_REDIS_URL` (the values are on the volume now).
4. `exec /app/entrypoint.sh` (unchanged upstream): unsets env overrides, `init_user_directories`, `chown -R deeptutor /app/data`, writability check, optional `DEEPTUTOR_APT_PACKAGES` / `DEEPTUTOR_EXTRAS`, exports the JSON settings, `exec supervisord`. Backend and frontend run as UID 1000.

**Build-time changes** (Dockerfile): appends `[eventlistener:railway_fatal_exit]` (stops supervisord when a program goes FATAL after its start retries, so the container restarts instead of staying half-alive) and `stopwaitsecs=30` to `[program:backend]` (supervisord's default is 10 s; DeepTutor drains running turns for up to 60 s). The `RUN` step fails the build if either edit did not apply.

**Variables** (all optional; none needs user input):

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `3782` | Next.js port; public domain + healthcheck |
| `DEEPTUTOR_ADMIN_USERNAME` | `admin` | |
| `DEEPTUTOR_ADMIN_PASSWORD` | `${{secret(32, alnum)}}` | Re-applied every boot; rotate by changing it |
| `DEEPTUTOR_TOKEN_EXPIRE_HOURS` | `24` | upstream default |
| `DEEPTUTOR_CORS_ORIGINS` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` | add custom domains |
| `DEEPTUTOR_WORKERS` | `2` | uvicorn workers; >1 requires Redis |
| `DEEPTUTOR_REDIS_URL` | `${{Redis.REDIS_URL}}` | private network |
| `DEEPTUTOR_REDIS_WAIT_SECONDS` | `300` | then 1-worker fallback |
| `DEEPTUTOR_ALLOW_CODE_EXEC` | `false` | see "Code execution" |
| `LLM_BINDING` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` | `openai` / empty / `gpt-5-mini` / empty | empty base URL = provider default |
| `EMBEDDING_BINDING` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `EMBEDDING_DIMENSION` | `openai` / empty (reuses `LLM_API_KEY` and `LLM_BASE_URL` for the same binding) / `text-embedding-3-small` / empty / empty | empty dimension = 1536 or 3072 for OpenAI's v3 models (recorded up front so a later "Test connection" does not change existing indexes' signature), else provider default |
| `BACKEND_HOST` | `127.0.0.1` | read by the image's `start-backend.sh`; keeps the API off the private network |
| `TZ` | `UTC` | |
| `DEEPTUTOR_EXTRAS`, `DEEPTUTOR_APT_PACKAGES` | empty | upstream's install-at-start hooks |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `45` | > backend `stopwaitsecs` 30 |

### Redis

`redis:8.2`, private, volume `/data`, start command from `_railway-base/redis.json` plus `--appendonly yes --appendfsync everysec` (upstream compose runs Redis with AOF so unflushed turn event and command streams survive a Redis restart). Password `${{secret(32)}}`; `REDIS_URL` referenced by DeepTutor. Listens on all IPv4 and IPv6 addresses (image default); redis-py tries every resolved address.

## Design decisions

### PocketBase: not included

Upstream's `docker-compose.yml` lists a PocketBase sidecar, but it is optional and upstream advises against it for this use:

- `deeptutor/multi_user/__init__.py` (v1.6.15): PocketBase mode is "single-user only"; the `users` collection has no `role` field, "so no admin can be created", and session queries are not filtered by user. With PocketBase on, the generated admin would log in as a plain user and could not open Settings (models, providers).
- Device logins and guardian credential resets are disabled in PocketBase mode (`api/routers/auth.py`, `multi_user.py`).
- `docs-for-user/CONTAINERIZATION.md`: "keep `integrations.pocketbase_url` blank for multi-user deployments".
- Upstream's official-image compose (`docker-compose.ghcr.yml`) and the hardened `compose.yaml` path both run without it; it would also use `ghcr.io/muchobien/pocketbase:latest` (unpinned, third-party), and `scripts/pb_setup.py` still uses the pre-0.23 collection schema format.

The supported path is DeepTutor's built-in JWT + bcrypt auth with a JSON/SQLite store, which this template configures. An unused PocketBase service would only add cost (Rule 4). `configure.py` keeps `pocketbase_url` empty.

### Sandbox runner: not included, code execution off by default

`sandbox-runner` (`Dockerfile.runner`) is not Docker-in-Docker; it is a small HTTP server that runs commands as a subprocess in its own hardened container. It does not fit Railway:

- It works on the app's files through a **shared volume** (`/workspace` read-only plus writable `/workspace/outputs`, and `data/cli-apps`). Railway volumes cannot be shared, so generated files would never reach the app.
- Its isolation comes from compose options Railway does not offer (`read_only`, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, `mem_limit`), and upstream publishes no runner image.

Without a runner and without `bwrap` (not in the image), DeepTutor picks its "restricted subprocess" backend only if `sandbox_allow_subprocess` is true. That backend scrubs the environment and confines the working directory but runs inside the app container (APPLICATION isolation), where the files hold provider keys and the JWT secret; DeepTutor then offers `exec` to admins only (`agents/loop/pipeline.py`). The template sets it **off** (`DEEPTUTOR_ALLOW_CODE_EXEC=false`), as the market template and this collection's DeerFlow package do. Lost while off: the office-document skills (docx/pptx/xlsx/pdf generation via `exec`) and code-running answers. Tutoring, knowledge bases, quizzes, notebooks, guided learning, research, visualizations (rendered in the browser) and document previews (LibreOffice in the app) do not depend on it.

### One public domain, no extra proxy

The Next.js server already rewrites `/api/*`, `/ws/*` (WebSocket upgrade included) and `/files/*` to the backend at request time (`web/proxy.ts`), so no Caddy/nginx is needed. The backend binds 127.0.0.1 (Rule 8.2 does not apply: only the in-container proxy connects to it, over IPv4 loopback, which is also the only peer the backend trusts for its frontend-host header). The frontend binds 0.0.0.0 (upstream default), which Railway's edge and healthcheck use; nothing connects to DeepTutor over the private network. No startup step resolves the service's own private hostname (Rule 8.13).

## Required input

None. The admin password and the Redis password are generated with `secret()`. DeepTutor boots, and login works, without any provider key; to tutor it needs a chat model, either `LLM_API_KEY` at deploy (recommended) or a provider added in Settings → Models after login. Knowledge bases additionally need an embedding model.

## First login

1. Open `https://{your-domain}`; you are redirected to `/login`. Sign in as `admin` with `DEEPTUTOR_ADMIN_PASSWORD` (DeepTutor service → Variables).
2. If no key was set: Settings → Models → add a provider and model; Settings → Embedding models → add one for knowledge bases.
3. Knowledge base → upload a PDF → wait for indexing → chat with the knowledge base selected, run a Quiz on it, save an answer to a notebook.
4. More accounts: `/admin/users` (the public sign-up endpoint returns 403 once an admin exists, and the admin always exists here).

## Improvements over market templates

1. **Pinned official image** (`1.6.15` by digest) instead of `:latest`.
2. **No required inputs**: generated admin password (32 chars) instead of a typed one; provider key optional.
3. **Any provider, plus embeddings**: `LLM_BINDING` covers DeepTutor's provider list; an embedding profile is seeded too, so knowledge bases (the "your own documents" part) work right after deploy with an OpenAI key.
4. **Settings choices respected**: the seeded profile is only activated when nothing else is active; the market template overrode the user's provider on every restart.
5. **Upstream stack with Redis**: Redis turn coordination with AOF and 2 API workers, as in upstream's compose files; with a bounded wait and a 1-worker fallback so a slow Redis cannot crash-loop the app (Rule 8.5).
6. **End-to-end healthcheck** `/api/auth/status` through the web proxy to the API (market: `/login`, web app only), 900 s window.
7. **Self-healing**: restart policy `ALWAYS` plus a supervisord FATAL listener, so a program that gave up restarting no longer leaves a running but broken container.
8. **Graceful redeploys**: backend gets 30 s to save running answers (`stopwaitsecs`), `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=45`.
9. **Secret hygiene**: seed secrets are removed from the process environment after they are written to the volume; settings files are 0600; no secret is logged.
10. **First-boot JWT secret race fixed** for multi-worker mode.
11. **Readable, reviewable start scripts** in the repo instead of a base64 blob; no runtime patching of upstream files; configurable `PORT`.
12. **Documented decisions** on PocketBase (left out, with upstream's reasons) and code execution (off, opt-in, risk stated).

## Resource estimate (not measured, see Open questions)

Railway prices: RAM $10/GB-month, vCPU $20/vCPU-month, volume $0.15/GB-month.

| Service | Idle RAM / CPU | Typical RAM / CPU | Volume |
|---|---|---|---|
| DeepTutor (Next.js + 2 uvicorn workers + supervisord) | 1.0–1.2 GB / 0.05 | 1.6–2.5 GB / 0.3 (document parsing, embedding batches, LibreOffice previews spike higher) | 1–3 GB (knowledge bases, indexes, notebooks) |
| Redis | 0.02 GB / ~0 | 0.05 GB / 0.01 | < 0.2 GB |

- **Idle**: ~1.15 GB × $10 + 0.05 vCPU × $20 + ~1.2 GB × $0.15 ≈ **$13/month**.
- **Typical** (one active learner daily, a few knowledge bases): ~2.0 GB × $10 + 0.3 vCPU × $20 + ~3 GB × $0.15 ≈ **$27/month**.
- `DEEPTUTOR_WORKERS=1` saves roughly 0.4–0.6 GB (≈ $4–6/month).
- LLM and embedding usage is billed by the provider.

Minimum plan: **Hobby**. Trial's 1 GB per service is below DeepTutor's idle footprint with two workers and leaves no room for indexing. The 5 GB Hobby volume holds many textbooks; very large libraries need Pro.

## Verification checklist

Docker was **not used** this round (coordinator instruction after the disk-full incident): no image was pulled, no Dockerfile was built, nothing was run in a container. Done offline:

- `bash -n` / `sh -n` on `entrypoint.sh`; `py_compile` of both Python files with Python 3.11.13 (the image's minor version) and 3.14.
- `configure.py`: 19 pytest cases with Python 3.11, bcrypt 5.0.0 and redis-py 6.4.0 (the image's `redis>=5,<7` range): fresh boot; 10 invalid inputs fail fast without writing files; unreachable Redis → 1 memory worker, password not logged; a fake Redis (PING/AUTH) → redis mode with 2 workers; LLM + embedding seeding (OpenAI v3 dimension recorded; gateway URL reused with the key); second boot rewrites nothing; model change; a profile chosen in Settings is kept; provider switch; foreign provider skips embeddings; corrupt JSON set aside; legacy `public_api_base` dropped; existing hash kept, new password re-hashed; auth-secret helper called. No upstream code was executed (stubs only).
- Event listener: real supervisord 4.3.0 with a program that always fails → FATAL → listener SIGTERMs supervisord → exit 0 in 8 s, other programs stopped cleanly.
- The image's real `programs.conf`, `supervisord.conf` and `/app/entrypoint.sh` were extracted from their GHCR layers; the Dockerfile's two edits were applied to a copy and parsed with supervisor's own config loader (backend `stopwaitsecs=30`, listener subscribed to `PROCESS_STATE_FATAL`).
- `template.json` converted with `_tools/to_serialized.py` (no required inputs reported); description 73 chars; every variable has a description; image tags `1.6.15` (GHCR) and `redis:8.2` (Docker Hub) exist; icon URL returns 200.

On the first Railway deploy (all **unverified**):

- [ ] Repo build succeeds (`FROM` by digest, the `RUN` checks pass). Record build and first-start time.
- [ ] DeepTutor logs: `[railway] Redis answered`, `API workers: 2; turn coordination: redis`, `admin user: admin; login required`; then upstream's `Auth enabled: true`, `API Base URL (proxy): http://127.0.0.1:8001`; supervisord spawns `backend`, `frontend`, `railway_fatal_exit`. supervisord's `CRIT Supervisor is running as root` line is expected (upstream design).
- [ ] Healthcheck `/api/auth/status` returns 200 `{"enabled": true, "authenticated": false, ...}`.
- [ ] `/` redirects to `/login`; `POST /api/auth/register` returns 403; `GET /api/auth/is_first_user` returns `false`.
- [ ] Login as `admin` works; Settings and `/admin/users` are reachable (admin role).
- [ ] With `LLM_API_KEY`: Settings → Models shows "Railway (openai)" active; a chat answer streams over the WebSocket (`/ws`).
- [ ] Knowledge base: upload a PDF, indexing completes with `text-embedding-3-small`, a chat with the KB cites it; a Quiz is generated and graded; an answer is saved to a notebook.
- [ ] Settings readiness/diagnostics show Redis coordination enabled and verified, 2 workers; log in, then reload several times (requests land on both workers, no unexpected logout).
- [ ] Redeploy: still logged in (JWT secret on the volume); knowledge bases, notebooks and sessions persist; `auth.json` keeps the same `password_hash` (no re-hash when the password is unchanged).
- [ ] From another service in the project, `curl http://deeptutor.railway.internal:8001/health/live` is refused (API on loopback).
- [ ] Optional: set `DEEPTUTOR_WORKERS=1` and clear `DEEPTUTOR_REDIS_URL`: boots in memory mode.

## Open questions

- **Nothing has run on Railway yet**; the image (818 MB compressed) was not pulled or built locally. Build time and cold start are unknown; 900 s is an estimate.
- **Memory per worker is unmeasured.** If 2 workers do not fit the budget or misbehave, set `DEEPTUTOR_WORKERS=1` (upstream's default). Multi-worker Redis mode is an upstream feature with its own tests, but it is less used than the single-worker default.
- **Login brute force**: upstream has no lockout on `/api/auth/login`, and its optional `guard` extra cannot tell clients apart behind the bundled web proxy (all traffic arrives from loopback). The 32-character generated password is the mitigation.
- **Default model ids** (`gpt-5-mini`, `text-embedding-3-small`) can go stale; both are variables.
- **Embedding base URL**: DeepTutor normalises an empty embedding URL to the provider's default endpoint when it loads the catalog (`normalize_embedding_endpoint_for_display`); the next boot sets it back to empty. Harmless (the resolved endpoint and the index signature are the same either way), but `model_catalog.json` is rewritten on each start. Inferred from the source, not observed.
- **DuckDuckGo fallback search** (no key) may be rate-limited from Railway IPs; add a search provider key in Settings if web search fails.
- **Extras at start** (`DEEPTUTOR_EXTRAS`) run `pip install` as root on every new container; large extras slow boots and count against the healthcheck window.
