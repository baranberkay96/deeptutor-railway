"""configure.py never fails the deploy on a variable value (stdlib unittest, bcrypt stubbed).

    python3 -I -m unittest discover -s services/deeptutor/tests
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

CONFIGURE = pathlib.Path(__file__).resolve().parent.parent / "railway" / "configure.py"
MANAGED_PREFIXES = ("DEEPTUTOR_", "LLM_", "EMBEDDING_")
GOOD_PASSWORD = "p" * 32


def fake_bcrypt() -> types.ModuleType:
    """Deterministic stand-in: hash = 'stub$' + salt + sha256(salt + password)."""
    module = types.ModuleType("bcrypt")

    def gensalt() -> bytes:
        return os.urandom(4).hex().encode()

    def hashpw(password: bytes, salt: bytes) -> bytes:
        salt = salt.split(b"$")[1] if salt.startswith(b"stub$") else salt
        return b"stub$" + salt + b"$" + hashlib.sha256(salt + password).hexdigest().encode()

    def checkpw(password: bytes, hashed: bytes) -> bool:
        if not hashed.startswith(b"stub$"):
            raise ValueError("Invalid salt")
        return hashpw(password, hashed) == hashed

    module.gensalt, module.hashpw, module.checkpw = gensalt, hashpw, checkpw
    return module


def load_configure(app_root: str) -> types.ModuleType:
    with mock.patch.dict(os.environ, {"DEEPTUTOR_APP_ROOT": app_root}):
        spec = importlib.util.spec_from_file_location("railway_configure", CONFIGURE)
        module = importlib.util.module_from_spec(spec)
        sys.modules["railway_configure"] = module
        spec.loader.exec_module(module)
    return module


class ConfigureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bcrypt_patch = mock.patch.dict(sys.modules, {"bcrypt": fake_bcrypt()})
        bcrypt_patch.start()
        self.addCleanup(bcrypt_patch.stop)
        self.cfg = load_configure(self.tmp.name)
        self.settings_dir = pathlib.Path(self.tmp.name) / "data" / "user" / "settings"

    def run_main(self, **env: str) -> tuple[int, str]:
        clean = {k: v for k, v in os.environ.items() if not k.startswith(MANAGED_PREFIXES) and k != "PORT"}
        values = {"DEEPTUTOR_ADMIN_PASSWORD": GOOD_PASSWORD, "DEEPTUTOR_REDIS_URL": "", **env}
        err = io.StringIO()
        with mock.patch.dict(os.environ, {**clean, **values}, clear=True), \
                mock.patch.object(self.cfg, "ensure_auth_secret"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = self.cfg.main()
        return code, err.getvalue()

    def read(self, name: str) -> dict:
        return json.loads((self.settings_dir / f"{name}.json").read_text())

    def test_defaults_start_cleanly(self) -> None:
        code, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", err)
        self.assertEqual(self.read("auth")["username"], "admin")
        self.assertEqual(self.read("system")["frontend_port"], 3782)

    def test_invalid_values_warn_and_fall_back(self) -> None:
        cases = {
            "DEEPTUTOR_WORKERS": ("two", "system", "backend_workers", 1),  # no Redis -> 1 worker
            "DEEPTUTOR_ALLOW_CODE_EXEC": ("maybe", "system", "sandbox_allow_subprocess", False),
            "PORT": ("8001", "system", "frontend_port", 3782),
            "DEEPTUTOR_ADMIN_USERNAME": ("John Smith", "auth", "username", "admin"),
            "DEEPTUTOR_TOKEN_EXPIRE_HOURS": ("99999", "auth", "token_expire_hours", 24 * 90),
            "DEEPTUTOR_REDIS_URL": ("http://redis:6379", "integrations", "turn_coordination", None),
        }
        for name, (value, document, key, expected) in cases.items():
            with self.subTest(name=name):
                code, err = self.run_main(**{name: value})
                self.assertEqual(code, 0)
                self.assertIn(f"WARNING: {name}", err)
                if expected is not None:
                    self.assertEqual(self.read(document)[key], expected)
                else:
                    self.assertEqual(self.read(document)[key]["backend"], "memory")

    def test_provider_values_are_repaired_or_dropped(self) -> None:
        code, err = self.run_main(LLM_API_KEY="sk-test", LLM_BINDING="OpenAI GPT",
                                  LLM_BASE_URL="api.example.com/v1", EMBEDDING_DIMENSION="abc")
        self.assertEqual(code, 0)
        for name in ("LLM_BINDING", "LLM_BASE_URL", "EMBEDDING_DIMENSION"):
            self.assertIn(f"WARNING: {name}", err)
        llm = self.read("model_catalog")["services"]["llm"]["profiles"][0]
        self.assertEqual((llm["binding"], llm["base_url"]), ("openai", "https://api.example.com/v1"))
        self.assertNotIn("sk-test", err)

    def test_unusable_password_locks_login_on_first_boot(self) -> None:
        for value in ("", "short12", "x" * 73):
            with self.subTest(length=len(value)):
                code, err = self.run_main(DEEPTUTOR_ADMIN_PASSWORD=value)
                self.assertEqual(code, 0)
                self.assertIn("WARNING: DEEPTUTOR_ADMIN_PASSWORD", err)
                stored = self.read("auth")["password_hash"].encode()
                bcrypt = sys.modules["bcrypt"]
                self.assertFalse(bcrypt.checkpw(value.encode(), stored))
                self.assertTrue(self.read("auth")["enabled"])
                (self.settings_dir / "auth.json").unlink()

    def test_unusable_password_keeps_the_stored_one(self) -> None:
        self.run_main()
        stored = self.read("auth")["password_hash"]
        code, err = self.run_main(DEEPTUTOR_ADMIN_PASSWORD="short")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("auth")["password_hash"], stored)

    def test_short_but_usable_password_is_applied_with_warning(self) -> None:
        code, err = self.run_main(DEEPTUTOR_ADMIN_PASSWORD="ninechars")
        self.assertEqual(code, 0)
        self.assertIn("fewer than 12 characters", err)
        stored = self.read("auth")["password_hash"].encode()
        self.assertTrue(sys.modules["bcrypt"].checkpw(b"ninechars", stored))


if __name__ == "__main__":
    unittest.main()
