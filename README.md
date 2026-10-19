# Deploy and Host DeepTutor with Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/deeptutor-v1-personal-ai-tutor?referralCode=20H68q)

DeepTutor is an open-source (Apache-2.0) AI tutor from HKU's Data Intelligence Lab. Upload your own textbooks and notes, then learn from them: ask questions with citations, generate quizzes, follow guided learning paths and keep notebooks. This community template runs DeepTutor v1.6.15 on Railway behind one HTTPS URL with a protected login.

## About Hosting DeepTutor

DeepTutor ships as one container that runs a FastAPI backend and a Next.js web app under supervisord, with all settings, accounts, knowledge bases and notes in one data folder. Hosting it well means pinning the image, putting that folder on a volume, keeping login on because the app holds your provider keys, and configuring it through its JSON settings files, which the image reads instead of environment variables. This template writes those settings from Railway variables on every start: it creates the admin account from a generated password, connects Redis for turn coordination with two API workers, keeps the API on loopback behind the web app, and can pre-select your LLM and embedding provider.

## Common Use Cases

- A private study companion that answers from your own PDFs, slides and notes, with citations
- Practice quizzes and question sets generated from a course's material, graded by the tutor
- Guided learning paths and books built from a knowledge base, with progress and notebooks
- A small class or study group: the admin creates learner accounts, each with a private workspace
- Deep research reports and visualizations on a topic, saved alongside your notes

## Dependencies for DeepTutor Hosting

- DeepTutor (included): web app on the public URL, API on loopback inside the same service, volume for all data
- Redis (included, private): turn coordination, event streams and multi-worker mode
- An API key for a chat model provider (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter and many others), set at deploy or later in Settings
- An embedding model for knowledge bases; with an OpenAI key the template sets one up automatically

### Deployment Dependencies

- DeepTutor repository and documentation: https://github.com/HKUDS/DeepTutor and https://deeptutor.info/
- Container image: https://github.com/HKUDS/DeepTutor/pkgs/container/deeptutor
- Container guide (settings files, PocketBase, security notes): https://github.com/HKUDS/DeepTutor/blob/main/docs-for-user/CONTAINERIZATION.md
- Redis: https://redis.io/docs/latest/

### Implementation Details

| Service | Source | Purpose |
|---|---|---|
| DeepTutor | `ghcr.io/hkuds/deeptutor:1.6.15` (digest-pinned) plus a small start script | Next.js web app on port 3782 (public, healthcheck `/api/auth/status`), FastAPI API on 127.0.0.1:8001 with 2 workers, volume `/app/data` |
| Redis | `redis:8.2` with append-only persistence | Turn coordination for the API workers; private, volume `/data` |

**First login**

1. Optional: set `LLM_API_KEY` when deploying (plus `LLM_BINDING` and `LLM_MODEL` for providers other than OpenAI; defaults are `openai` and `gpt-5-mini`). With an OpenAI key the same key also sets up `text-embedding-3-small` for knowledge bases. For other providers set `EMBEDDING_API_KEY` and `EMBEDDING_BINDING`, or configure embeddings in Settings.
2. Open `https://{your-domain}` and sign in as `admin` with `DEEPTUTOR_ADMIN_PASSWORD` from the DeepTutor service's Variables tab. The sign-up page is closed: only the admin can create accounts, on the Users admin page (`/admin/users`).
3. Without a key at deploy, open Settings → Models after login and add a provider and an embedding model.
4. Create a knowledge base, upload documents and wait for indexing, then chat with it, start a Quiz, or save answers to a notebook.

**What is not enabled**: code execution. DeepTutor's `exec` tool, which its office-document skills (Word, PowerPoint, Excel, PDF generation) and code-running answers use, normally runs in a separate sandbox container. Railway has no Docker-in-Docker, and that sidecar needs a volume shared with the app, which Railway does not allow. It is therefore off by default. Set `DEEPTUTOR_ALLOW_CODE_EXEC=true` to let admin chats run code as a restricted subprocess inside the DeepTutor container. That subprocess is not isolated from the app's data and API keys, so enable it only if you accept that.

**Scaling**: DeepTutor keeps its data on one volume, so it runs as one replica. Raise `DEEPTUTOR_WORKERS` (1 to 8, roughly 0.5 GB RAM each) for more parallel work such as document indexing, or give the service more RAM and CPU. More than one worker needs Redis; with `DEEPTUTOR_WORKERS=1` you can clear `DEEPTUTOR_REDIS_URL` and remove Redis. Redis itself stays small.

**Pinning and upgrades**: the image tag and digest are in `services/deeptutor/Dockerfile`. Back up the DeepTutor volume, change both to a newer release from the GitHub container registry, and redeploy. The start script re-applies the managed settings on every boot; everything else you change in Settings is kept.

**Security notes**: login is always required, the admin password is generated, the API listens only on loopback, session cookies are `Secure`, and API keys are stored in the settings files on the volume. Upstream has no login rate limit, so keep the generated password. Upstream's optional PocketBase mode is not used: upstream documents it as single-user, and every PocketBase login gets the non-admin role, so nobody could reach Settings.

### Why Deploy DeepTutor on Railway?

Railway runs DeepTutor and its Redis side by side with private networking, a persistent volume and HTTPS, so you get a private, login-protected AI tutor from one deploy, without managing a server. You pay for the resources it uses, and you can switch providers, add learners or scale workers from the dashboard.
