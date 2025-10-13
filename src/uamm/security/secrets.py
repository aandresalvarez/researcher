from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:  # optional dependency; we fall back to file-based stub when absent
    import hvac  # type: ignore[import]
except Exception:  # pragma: no cover - hvac is optional in tests
    hvac = None  # type: ignore[assignment]


class SecretError(RuntimeError):
    """Raised when required secrets cannot be loaded."""


@dataclass
class SecretSpec:
    alias: str
    env: Optional[str] = None
    vault_path: Optional[str] = None
    vault_key: Optional[str] = None
    required: bool = True

    @classmethod
    def from_dict(cls, alias: str, data: Dict[str, Any]) -> "SecretSpec":
        return cls(
            alias=alias,
            env=data.get("env"),
            vault_path=data.get("vault_path"),
            vault_key=data.get("vault_key"),
            required=bool(data.get("required", True)),
        )


class SecretManager:
    """Fetch secrets from Vault (preferred) with environment fallback.

    The manager does not expose concrete secret values in logs; callers request
    keys by alias. In local environments, secrets can be supplied via env vars
    or an optional stub file (JSON) pointed to by `vault_stub_file`.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        env_prefix: str,
        vault_addr: Optional[str],
        vault_token_env: Optional[str],
        vault_mount: Optional[str],
        vault_namespace: Optional[str],
        vault_stub_file: Optional[str],
        specs: Dict[str, SecretSpec],
        cache_ttl: int = 300,
    ) -> None:
        self._enabled = enabled
        self._env_prefix = env_prefix
        self._vault_addr = vault_addr
        self._vault_token_env = vault_token_env
        self._vault_mount = vault_mount or "secret"
        self._vault_namespace = vault_namespace
        self._vault_stub_file = vault_stub_file
        self._specs = specs
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, str] = {}
        self._cache_ts: float = 0.0
        self._lock = threading.Lock()
        self._vault_client: Any | None = None
        self._stub_payload: Dict[str, Dict[str, Any]] | None = None
        self._last_missing: Dict[str, SecretSpec] = {}

    @classmethod
    def from_settings(cls, settings: Any) -> "SecretManager":
        raw_specs = getattr(settings, "secrets", {}) or {}
        specs = {
            alias: SecretSpec.from_dict(alias, data or {})
            for alias, data in raw_specs.items()
        }
        return cls(
            enabled=bool(getattr(settings, "vault_enabled", False)),
            env_prefix=str(getattr(settings, "secret_env_prefix", "UAMM_SECRET_")),
            vault_addr=getattr(settings, "vault_addr", None),
            vault_token_env=getattr(settings, "vault_token_env", "VAULT_TOKEN"),
            vault_mount=getattr(settings, "vault_mount_point", "secret"),
            vault_namespace=getattr(settings, "vault_namespace", None),
            vault_stub_file=getattr(settings, "vault_stub_file", None),
            specs=specs,
            cache_ttl=int(getattr(settings, "secrets_cache_ttl_seconds", 300) or 300),
        )

    def bootstrap(self, *, strict: bool = True) -> Dict[str, str]:
        """Load all configured secrets into the cache.

        When `strict` is True, missing required secrets raise SecretError.
        """
        resolved: Dict[str, str] = {}
        missing: Dict[str, SecretSpec] = {}
        for alias, spec in self._specs.items():
            value = self._resolve(alias, spec)
            if value is None:
                if spec.required:
                    missing[alias] = spec
                continue
            resolved[alias] = value

        self._last_missing = missing
        with self._lock:
            self._cache = resolved
            self._cache_ts = time.time()

        if missing and strict:
            raise SecretError(
                f"missing required secrets: {', '.join(sorted(missing.keys()))}"
            )
        return resolved

    def get(self, alias: str, *, refresh: bool = False) -> Optional[str]:
        with self._lock:
            if (
                not refresh
                and self._cache
                and (time.time() - self._cache_ts) < self._cache_ttl
            ):
                return self._cache.get(alias)
        spec = self._specs.get(alias)
        if spec is None:
            return None
        value = self._resolve(alias, spec)
        if value is not None:
            with self._lock:
                self._cache[alias] = value
                self._cache_ts = time.time()
        return value

    @property
    def missing(self) -> Dict[str, SecretSpec]:
        return dict(self._last_missing)

    # Internal helpers -----------------------------------------------------

    def _resolve(self, alias: str, spec: SecretSpec) -> Optional[str]:
        value = self._from_env(alias, spec)
        if value is not None:
            return value
        if not self._enabled:
            return None
        return self._from_vault(spec)

    def _from_env(self, alias: str, spec: SecretSpec) -> Optional[str]:
        env_key = spec.env or f"{self._env_prefix}{alias.upper()}"
        value = os.getenv(env_key)
        if value is not None:
            return value
        return None

    def _from_vault(self, spec: SecretSpec) -> Optional[str]:
        path = spec.vault_path
        key = spec.vault_key
        if not path or not key:
            return None
        # Stub file fallback (useful for local/dev)
        if self._vault_stub_file:
            payload = self._load_stub_data()
            record = payload.get(path) or {}
            value = record.get(key)
            if value is not None:
                return str(value)
        client = self._ensure_vault_client()
        if client is None:
            return None
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self._vault_mount,
            )
        except Exception:
            return None
        data = (
            response.get("data", {}).get("data", {})
            if isinstance(response, dict)
            else {}
        )
        value = data.get(key)
        return str(value) if value is not None else None

    def _ensure_vault_client(self) -> Any | None:
        if self._vault_client is not None:
            return self._vault_client
        if hvac is None:
            return None
        token_env = self._vault_token_env or "VAULT_TOKEN"
        token = os.getenv(token_env)
        if not token or not self._vault_addr:
            return None
        client = hvac.Client(url=self._vault_addr, token=token)  # type: ignore[call-arg]
        if self._vault_namespace:
            client.namespace = self._vault_namespace  # type: ignore[attr-defined]
        self._vault_client = client
        return client

    def _load_stub_data(self) -> Dict[str, Dict[str, Any]]:
        if self._stub_payload is not None:
            return self._stub_payload
        path = Path(str(self._vault_stub_file))
        try:
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text) if text else {}
        except Exception:
            payload = {}
        self._stub_payload = {str(k): dict(v or {}) for k, v in payload.items()}
        return self._stub_payload


__all__ = ["SecretManager", "SecretSpec", "SecretError"]
