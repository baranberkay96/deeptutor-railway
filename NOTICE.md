# NOTICE: DeepTutor v1 Personal AI Tutor template

## Software used

| Component | Source | License | How it is used |
|---|---|---|---|
| DeepTutor | `ghcr.io/hkuds/deeptutor:1.6.15@sha256:602d5dfb…775c535` (official image, built from tag `v1.6.15`, commit `a31d826`), https://github.com/HKUDS/DeepTutor | Apache-2.0, Copyright 2025 Data Intelligence Lab, The University of Hong Kong (repository `LICENSE`; the repository's `THIRD_PARTY_NOTICES.md` covers code DeepTutor adapted from others) | Run as published. The template's Dockerfile adds files under `/opt/railway/`, appends one event-listener section to the image's `/etc/supervisor/conf.d/programs.conf` and adds `stopwaitsecs=30` to its `[program:backend]` section. No DeepTutor source file is copied or changed. |
| Redis | `redis:8.2` (Docker official image) | Redis Open Source 8: RSALv2, SSPLv1 or AGPLv3, at the user's choice | Run unmodified. Service definition from this collection's `_railway-base/redis.json`, with `--appendonly yes --appendfsync everysec` added as in upstream DeepTutor's compose files |
| DeepTutor logo | `web/public/logo.png` at tag `v1.6.15`, served by jsDelivr | Part of the DeepTutor repository | Used as the template and service icon to identify the app |

DeepTutor is licensed under the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0). This template redistributes no DeepTutor code; it references the official image by digest.

Product names and logos belong to their owners. This is a community template, not made, endorsed or supported by HKUDS or the University of Hong Kong.

## Files in this package

All files were written for this template:

- `services/deeptutor/Dockerfile`: `FROM` the pinned official image, copies `railway/` to `/opt/railway`, applies the two supervisord additions and checks that they took effect.
- `services/deeptutor/railway/entrypoint.sh`: removes the empty `lost+found` from the volume, runs `configure.py`, unsets the seed secrets, then `exec`s the image's own `/app/entrypoint.sh`.
- `services/deeptutor/railway/configure.py`: writes DeepTutor's runtime settings (`system.json`, `auth.json`, `integrations.json`, `model_catalog.json` under `data/user/settings/`) from Railway variables, waits for Redis, pre-creates the JWT secret. The JSON key names follow DeepTutor's settings schema as documented in `docs-for-user/CONTAINERIZATION.md` and defined in `deeptutor/services/config/runtime_settings.py` and `model_catalog.py`; no upstream code was copied.
- `services/deeptutor/railway/supervisor_fatal_exit.py`, `supervisor-railway.conf`: a supervisord event listener, written from supervisord's documented event-listener protocol (http://supervisord.org/events.html).

## Upstream files and docs checked (2026-10-20)

`_upstream/HKUDS__DeepTutor` was compared with tag `v1.6.15` through the GitHub API (git blob SHAs of 14 key files, all identical). Files read:

- Deployment: `Dockerfile` (production stage: supervisord programs, `start-backend.sh`, `start-frontend.sh`, `entrypoint.sh`), `Dockerfile.runner`, `docker-compose.yml`, `docker-compose.ghcr.yml`, `compose.yaml`, `.env.example`, `.github/workflows/docker-release.yml` (tag scheme), `docs-for-user/CONTAINERIZATION.md`, `docs-for-user/SECURITY_GUARD.md`, `assets/releases/ver1-6-15.md`.
- Settings and auth: `deeptutor/services/config/runtime_settings.py`, `model_catalog.py`, `provider_runtime.py`, `embedding_endpoint.py`, `origins.py`; `deeptutor/services/auth.py`, `deeptutor/api/routers/auth.py` (login, register, status), `deeptutor/multi_user/__init__.py` (PocketBase support matrix), `identity.py`, `paths.py`; `deeptutor/services/pocketbase_client.py`, `scripts/pb_setup.py`.
- Runtime: `deeptutor/api/main.py` (routes, CORS, `/health/ready`), `deeptutor/app/container.py`, `deeptutor/runtime/coordination/settings.py`, `deeptutor/services/sandbox/config.py`, `backends.py`, `deeptutor/agents/loop/pipeline.py` (exec gating), `deeptutor/services/workspace/service.py`, `deeptutor/services/setup/`, `scripts/install_extras.py`, `web/proxy.ts`, `web/lib/proxy-policy.ts`, `web/lib/backend-runtime-config.ts`, `web/lib/backend-forward.ts`, `web/next.config.js`.
- Registry: GHCR manifest list and amd64 config for `ghcr.io/hkuds/deeptutor:1.6.15` (= `latest` on 2026-10-20, label `org.opencontainers.image.revision=a31d826`); the image's own `/etc/supervisor/supervisord.conf`, `/etc/supervisor/conf.d/programs.conf` and `/app/entrypoint.sh` were extracted from their layers and read.

## Market templates and repositories reviewed

| Template / repo | License | Use |
|---|---|---|
| `deeptutor-personalized-ai-tutoring` ("DeepTutor - personalized AI tutoring", image `ghcr.io/hkuds/deeptutor:latest`, 1 active project) | No license stated | Its start command (a base64-encoded settings script) was decoded and read for ideas. Nothing was copied; this template's scripts were written independently. |

No README text from other templates was reused.
