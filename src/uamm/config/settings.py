from dataclasses import dataclass
import os
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

try:  # optional dependency
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None


@dataclass
class Settings:
    env: str = os.getenv("UAMM_ENV", "dev")
    config_path: str = os.getenv("UAMM_CONFIG_PATH", "config/settings.yaml")
    cp_enabled: bool = False
    cp_auto_enable: bool = True
    vault_enabled: bool = bool(int(os.getenv("UAMM_VAULT_ENABLED", "0")))
    vault_addr: str | None = os.getenv("UAMM_VAULT_ADDR")
    vault_token_env: str = os.getenv("UAMM_VAULT_TOKEN_ENV", "VAULT_TOKEN")
    vault_namespace: str | None = os.getenv("UAMM_VAULT_NAMESPACE")
    vault_mount_point: str = os.getenv("UAMM_VAULT_MOUNT_POINT", "secret")
    vault_stub_file: str | None = os.getenv("UAMM_VAULT_STUB_FILE")
    secret_env_prefix: str = os.getenv("UAMM_SECRET_PREFIX", "UAMM_SECRET_")
    secrets_cache_ttl_seconds: int = int(
        os.getenv("UAMM_SECRETS_CACHE_TTL_SECONDS", "300")
    )
    secrets: dict = None  # type: ignore[assignment]
    db_path: str = os.getenv("UAMM_DB_PATH", "data/uamm.sqlite")
    schema_path: str = os.getenv("UAMM_SCHEMA_PATH", "src/uamm/memory/schema.sql")
    stream_default: bool = True
    uq_mode: str = "snne"
    snne_samples: int = 5
    snne_tau: float = 0.3
    accept_threshold: float = 0.85
    borderline_delta: float = 0.05
    max_refinement_steps: int = 2
    cp_target_mis: float = 0.05
    steps_ttl_days: int = 90
    memory_ttl_days: int = 60
    table_allowed: list[str] = None  # type: ignore[assignment]
    table_policies: dict = None  # type: ignore[assignment]
    table_allowed_by_domain: dict = None  # type: ignore[assignment]
    rag_weight_sparse: float = 0.5
    rag_weight_dense: float = 0.5
    egress_block_private_ip: bool = True
    egress_enforce_tls: bool = True
    egress_allow_redirects: int = 3
    egress_max_payload_bytes: int = 5 * 1024 * 1024
    egress_allowlist_hosts: list[str] = None  # type: ignore[assignment]
    egress_denylist_hosts: list[str] = None  # type: ignore[assignment]
    vector_backend: str = os.getenv("UAMM_VECTOR_BACKEND", "none")
    lancedb_uri: str = os.getenv("UAMM_LANCEDB_URI", "data/lancedb")
    lancedb_table: str = os.getenv("UAMM_LANCEDB_TABLE", "rag_vectors")
    lancedb_metric: str = os.getenv("UAMM_LANCEDB_METRIC", "cosine")
    lancedb_k: int = int(os.getenv("UAMM_LANCEDB_K", "8"))
    # Local docs ingestion
    docs_dir: str = os.getenv("UAMM_DOCS_DIR", "data/docs")
    docs_auto_ingest: bool = bool(int(os.getenv("UAMM_DOCS_AUTO_INGEST", "1")))
    docs_scan_interval_seconds: int = int(os.getenv("UAMM_DOCS_SCAN_INTERVAL_SECONDS", "60"))
    docs_chunk_chars: int = int(os.getenv("UAMM_DOCS_CHUNK_CHARS", "1400"))
    docs_overlap_chars: int = int(os.getenv("UAMM_DOCS_OVERLAP_CHARS", "200"))
    docs_chunk_mode: str = os.getenv("UAMM_DOCS_CHUNK_MODE", "chars")  # 'chars'|'tokens'
    docs_chunk_tokens: int = int(os.getenv("UAMM_DOCS_CHUNK_TOKENS", "600"))
    docs_overlap_tokens: int = int(os.getenv("UAMM_DOCS_OVERLAP_TOKENS", "100"))
    docs_ocr_enabled: bool = bool(int(os.getenv("UAMM_DOCS_OCR_ENABLED", "1")))
    docs_tables_enabled: bool = bool(int(os.getenv("UAMM_DOCS_TABLES_ENABLED", "0")))
    # Memory promotion
    memory_promotion_enabled: bool = bool(int(os.getenv("UAMM_MEMORY_PROMOTION_ENABLED", "0")))
    memory_promotion_min_support: int = int(os.getenv("UAMM_MEMORY_PROMOTION_MIN_SUPPORT", "3"))
    # Planning (selective search over thoughts)
    planning_enabled: bool = bool(int(os.getenv("UAMM_PLANNING_ENABLED", "0")))
    planning_mode: str = os.getenv("UAMM_PLANNING_MODE", "tot")
    planning_budget: int = int(os.getenv("UAMM_PLANNING_BUDGET", "3"))
    planning_when: str = os.getenv("UAMM_PLANNING_WHEN", "borderline")  # 'always'|'borderline'|'iterate'
    # Faithfulness
    faithfulness_enabled: bool = bool(int(os.getenv("UAMM_FAITHFULNESS_ENABLED", "1")))
    faithfulness_threshold: float = float(os.getenv("UAMM_FAITHFULNESS_THRESHOLD", "0.6"))
    faithfulness_use_llm: bool = bool(int(os.getenv("UAMM_FAITHFULNESS_USE_LLM", "0")))
    # Guardrails
    guardrails_enabled: bool = bool(int(os.getenv("UAMM_GUARDRAILS_ENABLED", "0")))
    guardrails_config_path: str | None = os.getenv("UAMM_GUARDRAILS_CONFIG_PATH")
    # Token cost estimate for evals (USD per 1K tokens)
    token_cost_per_1k: float = float(os.getenv("UAMM_TOKEN_COST_PER_1K", "0.0"))
    # MCP
    mcp_enabled: bool = bool(int(os.getenv("UAMM_MCP_ENABLED", "0")))
    mcp_bind: str = os.getenv("UAMM_MCP_BIND", "127.0.0.1")
    mcp_port: int = int(os.getenv("UAMM_MCP_PORT", "8765"))
    mcp_tools_expose: list[str] = None  # type: ignore[assignment]
    # Seed admin (first-run)
    seed_admin_enabled: bool = bool(int(os.getenv("UAMM_SEED_ADMIN_ENABLED", "0")))
    seed_admin_workspace: str = os.getenv("UAMM_SEED_ADMIN_WORKSPACE", "default")
    seed_admin_label: str = os.getenv("UAMM_SEED_ADMIN_LABEL", "seed-admin")
    seed_admin_key: str | None = os.getenv("UAMM_SEED_ADMIN_KEY")
    seed_admin_autogen: bool = bool(int(os.getenv("UAMM_SEED_ADMIN_AUTOGEN", "0")))
    # Rate limiting
    rate_limit_enabled: bool = bool(int(os.getenv("UAMM_RATE_LIMIT_ENABLED", "0")))
    rate_limit_per_minute: int = int(os.getenv("UAMM_RATE_LIMIT_PER_MINUTE", "120"))
    rate_limit_viewer_per_minute: int | None = (
        int(os.getenv("UAMM_RATE_LIMIT_VIEWER_PER_MINUTE", "0")) or None
    )
    rate_limit_editor_per_minute: int | None = (
        int(os.getenv("UAMM_RATE_LIMIT_EDITOR_PER_MINUTE", "0")) or None
    )
    rate_limit_admin_per_minute: int | None = (
        int(os.getenv("UAMM_RATE_LIMIT_ADMIN_PER_MINUTE", "0")) or None
    )
    # Approvals (tool pause/resume) — stub
    approvals_ttl_seconds: int = 1800
    tools_requiring_approval: list[str] = None  # type: ignore[assignment]
    cp_alert_tolerance: float = float(os.getenv("UAMM_CP_ALERT_TOLERANCE", "0.02"))
    approvals_pending_alert_threshold: int = int(
        os.getenv("UAMM_APPROVALS_PENDING_ALERT_THRESHOLD", "5")
    )
    approvals_pending_age_threshold_seconds: int = int(
        os.getenv("UAMM_APPROVALS_PENDING_AGE_THRESHOLD_SECONDS", "300")
    )
    snne_drift_quantile_tolerance: float = float(
        os.getenv("UAMM_SNNE_DRIFT_QUANTILE_TOLERANCE", "0.08")
    )
    snne_drift_min_samples: int = int(os.getenv("UAMM_SNNE_DRIFT_MIN_SAMPLES", "50"))
    snne_drift_window: int = int(os.getenv("UAMM_SNNE_DRIFT_WINDOW", "200"))
    latency_p95_alert_seconds: float = float(
        os.getenv("UAMM_LATENCY_P95_ALERT_SECONDS", "6.0")
    )
    latency_alert_min_requests: int = int(
        os.getenv("UAMM_LATENCY_ALERT_MIN_REQUESTS", "20")
    )
    abstain_alert_rate: float = float(os.getenv("UAMM_ABSTAIN_ALERT_RATE", "0.3"))
    abstain_alert_min_answers: int = int(
        os.getenv("UAMM_ABSTAIN_ALERT_MIN_ANSWERS", "20")
    )
    tuner_proposal_ttl_seconds: int = int(
        os.getenv("UAMM_TUNER_PROPOSAL_TTL_SECONDS", "3600")
    )
    # Auth
    auth_required: bool = bool(int(os.getenv("UAMM_AUTH_REQUIRED", "0")))
    api_key_header: str = os.getenv("UAMM_API_KEY_HEADER", "X-API-Key")
    api_key_prefix: str = os.getenv("UAMM_API_KEY_PREFIX", "wk_")
    # Workspaces (multi-root)
    workspace_mode: str = os.getenv("UAMM_WORKSPACE_MODE", "single")
    # Comma-separated list of allowed base dirs; empty means no restriction (dev)
    workspace_base_dirs_raw: str = os.getenv("UAMM_WORKSPACE_BASE_DIRS", "")
    workspace_restrict_to_bases: bool = bool(
        int(os.getenv("UAMM_WORKSPACE_RESTRICT_TO_BASES", "0"))
    )
    # Derived list for convenience (populated in load_settings)
    workspace_base_dirs: list[str] = None  # type: ignore[assignment]


def load_settings() -> Settings:
    env_file = os.getenv("UAMM_ENV_FILE", ".env")
    if load_dotenv is not None:
        load_dotenv(env_file)
    s = Settings()
    cfg_path = Path(s.config_path)
    if cfg_path.exists() and yaml is not None:
        with cfg_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if not hasattr(s, k):
                continue
            # Environment variables take precedence over YAML for core paths
            if k == "db_path" and os.getenv("UAMM_DB_PATH"):
                continue
            if k == "schema_path" and os.getenv("UAMM_SCHEMA_PATH"):
                continue
            setattr(s, k, v)
    if s.table_allowed is None:
        s.table_allowed = []
    if s.table_policies is None:
        s.table_policies = {}
    if s.table_allowed_by_domain is None:
        s.table_allowed_by_domain = {}
    if s.egress_allowlist_hosts is None:
        s.egress_allowlist_hosts = []
    if s.egress_denylist_hosts is None:
        s.egress_denylist_hosts = []
    if s.tools_requiring_approval is None:
        s.tools_requiring_approval = []
    if s.mcp_tools_expose is None:
        s.mcp_tools_expose = ["WEB_SEARCH", "WEB_FETCH", "MATH_EVAL", "TABLE_QUERY", "UAMM_ANSWER"]
    if s.secrets is None:
        s.secrets = {}
    if not s.vector_backend:
        s.vector_backend = "none"
    s.vector_backend = str(s.vector_backend).lower()
    # Derive workspace base dirs list
    if s.workspace_base_dirs is None:
        raw = (s.workspace_base_dirs_raw or "").strip()
        s.workspace_base_dirs = [p for p in (x.strip() for x in raw.split(",")) if p]
    # Default restriction: enable in non-dev if not explicitly set
    try:
        if str(s.env).lower() not in {"dev", "test"} and os.getenv("UAMM_WORKSPACE_RESTRICT_TO_BASES") is None:
            s.workspace_restrict_to_bases = True
    except Exception:
        pass
    return s
