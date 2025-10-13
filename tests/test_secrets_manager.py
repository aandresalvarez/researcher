import json
from types import SimpleNamespace

import pytest

from uamm.security.secrets import SecretManager, SecretError


def _settings(**overrides):
    base = {
        "env": "dev",
        "vault_enabled": False,
        "vault_addr": None,
        "vault_token_env": "VAULT_TOKEN",
        "vault_mount_point": "secret",
        "vault_namespace": None,
        "vault_stub_file": None,
        "secret_env_prefix": "UAMM_SECRET_",
        "secrets_cache_ttl_seconds": 5,
        "secrets": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_secret_manager_reads_from_env(monkeypatch):
    monkeypatch.setenv("TEST_ONE", "secret-value")
    settings = _settings(
        secret_env_prefix="TEST_",
        secrets={"one": {"env": "TEST_ONE", "required": True}},
    )
    mgr = SecretManager.from_settings(settings)
    resolved = mgr.bootstrap()
    assert resolved["one"] == "secret-value"
    assert mgr.get("one") == "secret-value"


def test_secret_manager_uses_stub_file(tmp_path):
    payload = {"uamm/sql": {"password": "stub-pass"}}
    stub_path = tmp_path / "vault.json"
    stub_path.write_text(json.dumps(payload), encoding="utf-8")
    settings = _settings(
        vault_enabled=True,
        vault_stub_file=str(stub_path),
        secrets={
            "db_password": {
                "vault_path": "uamm/sql",
                "vault_key": "password",
                "required": True,
            }
        },
    )
    mgr = SecretManager.from_settings(settings)
    resolved = mgr.bootstrap(strict=True)
    assert resolved["db_password"] == "stub-pass"


def test_secret_manager_raises_when_required_missing():
    settings = _settings(
        vault_enabled=False,
        secrets={"missing": {"required": True}},
    )
    mgr = SecretManager.from_settings(settings)
    with pytest.raises(SecretError):
        mgr.bootstrap(strict=True)
