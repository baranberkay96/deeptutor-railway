#!/usr/bin/env python3
"""Write DeepTutor's runtime settings from Railway variables, then exit.

Runs as root on every start, before the image's own /app/entrypoint.sh.

DeepTutor's Docker image is configured through JSON files, not environment
variables: the image sets DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=1, and its
entrypoint unsets AUTH_*, POCKETBASE_*, BACKEND_* and similar names, then
re-exports them from data/user/settings/*.json. This script therefore edits
those files:

  system.json        ports, worker count, CORS origin, code-execution switch
  auth.json          login on, admin username and bcrypt hash, secure cookie
  integrations.json  Redis turn coordination, PocketBase off
  model_catalog.json optional LLM and embedding profiles (LLM_*, EMBEDDING_*)

Only the keys named below are managed. Everything else in these files, and
everything changed later in DeepTutor's Settings page outside these keys, is
kept. DeepTutor fills in its defaults and normalises the files when it loads
them. No secret value is ever printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Optional

APP_ROOT = Path(os.environ.get("DEEPTUTOR_APP_ROOT", "/app"))
SETTINGS_DIR = APP_ROOT / "data" / "user" / "settings"

BACKEND_PORT = 8001
MAX_WORKERS = 8
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_BYTES = 72  # bcrypt only uses the first 72 bytes
REDIS_RETRY_SECONDS = 5
LLM_PROFILE_ID = "railway-llm"
EMBEDDING_PROFILE_ID = "railway-embedding"
DEFAULT_LLM_MODEL = "gpt-5-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
# Native sizes of OpenAI's v3 embedding models. Recording them up front keeps a
# knowledge base's embedding signature stable; with an empty dimension,
# DeepTutor fills it in on the first "Test connection", which changes the
# signature of indexes built before that.
KNOWN_EMBEDDING_DIMENSIONS = {
    ("openai", "text-embedding-3-small"): "1536",
    ("openai", "text-embedding-3-large"): "3072",
}

TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
FALSE_WORDS = frozenset({"0", "false", "no", "off"})
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BINDING_RE = re.compile(r"^[a-z0-9_]{2,40}$")
URL_RE = re.compile(r"^https?://[^\s/]+(/\S*)?$")
ORIGIN_SPLIT_RE = re.compile(r"[\s,;]+")


class ConfigError(Exception):
    """A Railway variable has a value DeepTutor cannot start with."""


def log(message: str) -> None:
    print(f"[railway] {message}", flush=True)


# ---------------------------------------------------------------------------
# Reading and validating Railway variables
# ---------------------------------------------------------------------------


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in TRUE_WORDS:
        return True
    if raw in FALSE_WORDS:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def env_int(name: str, default: int, low: int, high: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


def env_url(name: str) -> str:
    value = env_str(name).rstrip("/")
    if value and not URL_RE.match(value):
        raise ConfigError(f"{name} must be an http(s) URL, got {value!r}")
    return value


def env_binding(name: str, default: str) -> str:
    value = env_str(name, default).lower().replace("-", "_") or default
    if not BINDING_RE.match(value):
        raise ConfigError(f"{name} must be a DeepTutor provider id such as openai, got {value!r}")
    return value


@dataclass(frozen=True)
class ProviderInput:
    binding: str
    api_key: str
    model: str
    base_url: str
    dimension: str = ""


@dataclass(frozen=True)
class Settings:
    port: int
    workers: int
    cors_origins: tuple[str, ...]
    allow_code_exec: bool
    admin_username: str
    admin_password: str
    token_hours: int
    redis_url: str
    redis_wait_seconds: int
    llm: Optional[ProviderInput]
    embedding: Optional[ProviderInput]


def parse_origins(raw: str) -> tuple[str, ...]:
    origins: list[str] = []
    for item in ORIGIN_SPLIT_RE.split(raw):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        if not re.match(r"^https?://[^/\s]+$", origin):
            log(f"ignoring DEEPTUTOR_CORS_ORIGINS entry {origin!r} (expected https://host)")
            continue
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def admin_credentials() -> tuple[str, str]:
    username = env_str("DEEPTUTOR_ADMIN_USERNAME", "admin") or "admin"
    if not (USERNAME_RE.match(username) or EMAIL_RE.match(username)):
        raise ConfigError(
            "DEEPTUTOR_ADMIN_USERNAME must be 3-64 letters, digits, '.', '_' or '-', or an email address"
        )
    password = os.environ.get("DEEPTUTOR_ADMIN_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ConfigError(
            f"DEEPTUTOR_ADMIN_PASSWORD must have at least {MIN_PASSWORD_LENGTH} characters. "
            "Refusing to start: DeepTutor would otherwise run without a usable admin login."
        )
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ConfigError(f"DEEPTUTOR_ADMIN_PASSWORD must be at most {MAX_PASSWORD_BYTES} bytes")
    return username, password


def llm_input() -> Optional[ProviderInput]:
    api_key = env_str("LLM_API_KEY")
    if not api_key:
        return None
    return ProviderInput(
        binding=env_binding("LLM_BINDING", "openai"),
        api_key=api_key,
        model=env_str("LLM_MODEL", DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
        base_url=env_url("LLM_BASE_URL"),
    )


def embedding_input(llm: Optional[ProviderInput]) -> Optional[ProviderInput]:
    binding = env_binding("EMBEDDING_BINDING", "openai")
    api_key = env_str("EMBEDDING_API_KEY")
    if not api_key and llm is not None and llm.binding == binding:
        api_key = llm.api_key
    if not api_key:
        return None
    model = env_str("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL) or DEFAULT_EMBEDDING_MODEL
    dimension = env_str("EMBEDDING_DIMENSION")
    if dimension and not (dimension.isdigit() and int(dimension) > 0):
        raise ConfigError(f"EMBEDDING_DIMENSION must be a positive number or empty, got {dimension!r}")
    base_url = env_url("EMBEDDING_BASE_URL")
    if not base_url and llm is not None and api_key == llm.api_key:
        # Same key as the chat model: also use its endpoint (an OpenAI-compatible
        # gateway); DeepTutor appends /embeddings to a .../v1 base itself.
        base_url = llm.base_url
    return ProviderInput(
        binding=binding,
        api_key=api_key,
        model=model,
        base_url=base_url,
        dimension=dimension or KNOWN_EMBEDDING_DIMENSIONS.get((binding, model), ""),
    )


def load_settings() -> Settings:
    port = env_int("PORT", 3782, 1, 65535)
    if port == BACKEND_PORT:
        raise ConfigError(f"PORT must not be {BACKEND_PORT}; the DeepTutor API uses that port inside the container")
    redis_url = env_str("DEEPTUTOR_REDIS_URL")
    if redis_url and not redis_url.startswith(("redis://", "rediss://")):
        raise ConfigError("DEEPTUTOR_REDIS_URL must start with redis:// or rediss://")
    username, password = admin_credentials()
    llm = llm_input()
    return Settings(
        port=port,
        workers=env_int("DEEPTUTOR_WORKERS", 2, 1, MAX_WORKERS),
        cors_origins=parse_origins(env_str("DEEPTUTOR_CORS_ORIGINS")),
        allow_code_exec=env_bool("DEEPTUTOR_ALLOW_CODE_EXEC", False),
        admin_username=username,
        admin_password=password,
        token_hours=env_int("DEEPTUTOR_TOKEN_EXPIRE_HOURS", 24, 1, 24 * 90),
        redis_url=redis_url,
        redis_wait_seconds=env_int("DEEPTUTOR_REDIS_WAIT_SECONDS", 300, 0, 3600),
        llm=llm,
        embedding=embedding_input(llm),
    )


# ---------------------------------------------------------------------------
# JSON files
# ---------------------------------------------------------------------------


def _set_aside(path: Path, reason: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.invalid-{stamp}")
    path.replace(backup)
    log(f"{path.name} {reason}; moved it to {backup.name} and continued with DeepTutor's defaults")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _set_aside(path, "is not valid JSON")
        return {}
    if not isinstance(data, dict):
        _set_aside(path, "does not hold a JSON object")
        return {}
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write, owner-only from creation (the files hold keys and hashes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.railway-tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def update_json(name: str, change: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    path = SETTINGS_DIR / f"{name}.json"
    current = read_json(path)
    updated = change(current)
    if updated != current or not path.exists():
        write_json(path, updated)
    return updated


# ---------------------------------------------------------------------------
# Redis turn coordination
# ---------------------------------------------------------------------------


def redis_answers(url: str, wait_seconds: int) -> bool:
    """PING Redis until it answers or the wait runs out. Never logs the URL."""
    try:
        import redis  # shipped in the DeepTutor image (requirements/server.txt)
    except ImportError:
        log("the redis Python package is missing from the image")
        return False
    deadline = time.monotonic() + wait_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
            try:
                if client.ping():
                    log(f"Redis answered (attempt {attempt})")
                    return True
            finally:
                client.close()
        except Exception as exc:  # connection, DNS, auth or URL errors
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        else:
            reason = "no PONG"
        if time.monotonic() >= deadline:
            log(f"Redis did not answer within {wait_seconds} s ({reason})")
            return False
        if attempt == 1 or attempt % 6 == 0:
            log(f"waiting for Redis ({reason})")
        time.sleep(REDIS_RETRY_SECONDS)


def coordination(settings: Settings) -> tuple[str, str, int]:
    """Return (backend, redis_url, workers) for DeepTutor's turn coordinator.

    DeepTutor refuses to start more than one worker without Redis, and its
    backend refuses to start when the configured Redis is unreachable. Rather
    than crash-looping, fall back to one in-memory worker and say so.
    """
    if not settings.redis_url:
        if settings.workers > 1:
            log("DEEPTUTOR_REDIS_URL is empty: running 1 worker with in-memory turn coordination")
        return "memory", "", 1
    if redis_answers(settings.redis_url, settings.redis_wait_seconds):
        return "redis", settings.redis_url, settings.workers
    log("starting with 1 worker and in-memory turn coordination; redeploy DeepTutor once Redis is healthy")
    return "memory", "", 1


# ---------------------------------------------------------------------------
# Settings documents (pure functions: current dict in, new dict out)
# ---------------------------------------------------------------------------


def apply_system(current: dict[str, Any], settings: Settings, workers: int) -> dict[str, Any]:
    # ``public_api_base`` is a legacy alias DeepTutor folds into the external
    # API base; drop it so the frontend always proxies to the loopback API.
    kept = {key: value for key, value in current.items() if key != "public_api_base"}
    return {
        **kept,
        "backend_port": BACKEND_PORT,
        "backend_workers": workers,
        "frontend_port": settings.port,
        "next_public_api_base": "",
        "next_public_api_base_external": "",
        "cors_origin": "",
        "cors_origins": list(settings.cors_origins),
        "sandbox_allow_subprocess": settings.allow_code_exec,
    }


def password_hash(password: str, existing: str) -> str:
    import bcrypt  # shipped in the DeepTutor image

    if existing:
        try:
            if bcrypt.checkpw(password.encode("utf-8"), existing.encode("utf-8")):
                return existing
        except ValueError:
            pass
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def apply_auth(current: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        **current,
        "enabled": True,
        "username": settings.admin_username,
        "password_hash": password_hash(settings.admin_password, str(current.get("password_hash") or "")),
        "token_expire_hours": settings.token_hours,
        "cookie_secure": True,
    }


def apply_integrations(current: dict[str, Any], backend: str, redis_url: str) -> dict[str, Any]:
    previous = current.get("turn_coordination")
    turn = {**(previous if isinstance(previous, dict) else {}), "backend": backend, "redis_url": redis_url}
    # PocketBase mode is single-user upstream (every login gets role "user",
    # so nobody can reach Settings). This template does not ship PocketBase.
    return {**current, "pocketbase_url": "", "turn_coordination": turn}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "model"


def upsert_model(models: list[Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    kept = [model for model in models if isinstance(model, dict)]
    if any(model.get("id") == entry["id"] for model in kept):
        return [{**model, **entry} if model.get("id") == entry["id"] else model for model in kept]
    return [*kept, entry]


def upsert_profile(service: dict[str, Any], profile: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Add or refresh a Railway-managed profile and decide whether it is active.

    The profile becomes active when nothing valid is active yet, or when it is
    already the active one (so a changed model variable takes effect). A
    profile the user picked in Settings is never replaced.
    """
    profiles = [item for item in service.get("profiles", []) if isinstance(item, dict)]
    existing = next((item for item in profiles if item.get("id") == profile["id"]), None)
    if existing is None:
        merged = {"api_version": "", "extra_headers": {}, **profile, "models": [model]}
        new_profiles = [*profiles, merged]
    else:
        # Keep a name given in Settings; the variables stay authoritative for
        # provider, URL and key. A linked "connection" would override the key,
        # so the Railway profile is kept standalone.
        # A changed provider also drops the old models and the derived API
        # format, which DeepTutor recomputes from the binding.
        same_provider = existing.get("binding") == profile["binding"]
        dropped = {"connection_id"} if same_provider else {"connection_id", "api_format", "wire_api"}
        base = {key: value for key, value in existing.items() if key not in dropped}
        fields = {key: value for key, value in profile.items() if key != "name"}
        models = upsert_model(base.get("models", []), model) if same_provider else [model]
        merged = {**base, **fields, "models": models}
        new_profiles = [merged if item.get("id") == profile["id"] else item for item in profiles]
    active = service.get("active_profile_id")
    take_over = active == profile["id"] or active not in {item.get("id") for item in profiles}
    updated = {**service, "profiles": new_profiles}
    if take_over:
        updated = {**updated, "active_profile_id": profile["id"], "active_model_id": model["id"]}
    return updated


def llm_profile(provider: ProviderInput) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = {
        "id": LLM_PROFILE_ID,
        "name": f"Railway ({provider.binding})",
        "binding": provider.binding,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
    }
    model = {"id": f"{LLM_PROFILE_ID}-{slug(provider.model)}", "name": provider.model, "model": provider.model}
    return profile, model


def embedding_profile(provider: ProviderInput) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = {
        "id": EMBEDDING_PROFILE_ID,
        "name": f"Railway ({provider.binding})",
        "binding": provider.binding,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
    }
    model: dict[str, Any] = {
        "id": f"{EMBEDDING_PROFILE_ID}-{slug(provider.model)}",
        "name": provider.model,
        "model": provider.model,
    }
    if provider.dimension:
        model = {**model, "dimension": provider.dimension}
    return profile, model


def apply_catalog(current: dict[str, Any], settings: Settings) -> dict[str, Any]:
    services = current.get("services") if isinstance(current.get("services"), dict) else {}
    updated_services = dict(services)
    for name, provider, build in (
        ("llm", settings.llm, llm_profile),
        ("embedding", settings.embedding, embedding_profile),
    ):
        if provider is None:
            continue
        service = services.get(name) if isinstance(services.get(name), dict) else {}
        profile, model = build(provider)
        updated_services[name] = upsert_profile(service, profile, model)
    if updated_services == services and current:
        return current
    return {"version": 1, "connections": [], **current, "services": updated_services}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def ensure_auth_secret() -> None:
    """Create DeepTutor's JWT signing secret before uvicorn forks its workers.

    Otherwise, on the very first boot, every worker creates the file at import
    time and the last writer wins: logins signed by another worker are then
    rejected until the next restart.
    """
    sys.path.insert(0, str(APP_ROOT))
    try:
        from deeptutor.multi_user.identity import load_or_create_auth_secret

        load_or_create_auth_secret()
    except Exception as exc:  # the app creates it itself on start
        log(f"could not pre-create the auth secret ({type(exc).__name__}); DeepTutor will create it")


def describe(settings: Settings, backend: str, workers: int) -> None:
    llm = f"{settings.llm.binding}/{settings.llm.model}" if settings.llm else "not set (add one in Settings)"
    emb = (
        f"{settings.embedding.binding}/{settings.embedding.model}"
        if settings.embedding
        else "not set (needed for knowledge bases)"
    )
    log(f"admin user: {settings.admin_username}; login required")
    log(f"API workers: {workers}; turn coordination: {backend}")
    log(f"code execution (exec tool): {'admin only, restricted subprocess' if settings.allow_code_exec else 'off'}")
    log(f"LLM: {llm}; embeddings: {emb}")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[railway] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    backend, redis_url, workers = coordination(settings)
    update_json("system", lambda current: apply_system(current, settings, workers))
    update_json("auth", lambda current: apply_auth(current, settings))
    update_json("integrations", lambda current: apply_integrations(current, backend, redis_url))
    if settings.llm or settings.embedding:
        update_json("model_catalog", lambda current: apply_catalog(current, settings))
    ensure_auth_secret()
    describe(settings, backend, workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
