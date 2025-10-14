import logging
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from .routes import router as api_router
from uamm.config.settings import load_settings
from uamm.storage.db import ensure_schema, ensure_migrations
from uamm.api.state import (
    IdempotencyStore,
    ApprovalsStore,
    CPThresholdCache,
    TunerProposalStore,
)
from uamm.policy import cp_store
from uamm.security.secrets import SecretManager, SecretError
from uamm.rag.ingest import scan_folder
from uamm.security.auth import lookup_key, parse_bearer
from uamm.security.auth import count_keys, insert_api_key, new_key
from uamm.storage.workspaces import resolve_paths as ws_resolve_paths


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        start = time.time()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        dur_ms = int((time.time() - start) * 1000)
        logging.getLogger("uamm").info(
            "req", extra={"path": request.url.path, "rid": rid, "ms": dur_ms}
        )
        return response


class WorkspaceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Simple workspace/user context from headers; defaults are safe
        if not hasattr(request.state, "workspace"):
            request.state.workspace = (
                request.headers.get("X-Workspace", "default").strip() or "default"
            )
        if not hasattr(request.state, "user"):
            request.state.user = (
                request.headers.get("X-User", "anonymous").strip() or "anonymous"
            )
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        # Extract API key from header or Authorization
        key = request.headers.get(settings.api_key_header)
        if not key:
            # be robust to case-differences across clients
            target = settings.api_key_header.lower()
            for k, v in request.headers.items():
                if k.lower() == target:
                    key = v
                    break
        if not key:
            key = parse_bearer(request.headers.get("Authorization"))
        if key:
            rec = lookup_key(settings.db_path, key)
            if rec and rec.active:
                # Bind workspace and role from API key
                request.state.workspace = rec.workspace
                request.state.role = rec.role
                request.state.user = f"key:{rec.label}" if rec.label else "key:unknown"
        # If auth_required, enforce presence of valid key on protected writes at route level
        return await call_next(request)


class WorkspacePathMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Resolve per-workspace paths (db/docs/vectors) based on workspace record
        try:
            settings = request.app.state.settings
            slug = getattr(request.state, "workspace", None) or request.headers.get(
                "X-Workspace", "default"
            )
            paths = ws_resolve_paths(settings.db_path, slug, settings)
            # Attach to request.state for downstream usage
            request.state.db_path = paths.get("db_path", settings.db_path)
            request.state.docs_dir = paths.get("docs_dir", settings.docs_dir)
            request.state.lancedb_uri = paths.get("lancedb_uri", settings.lancedb_uri)
        except Exception:
            # Best-effort; fall back to settings
            settings = request.app.state.settings
            request.state.db_path = getattr(settings, "db_path", "data/uamm.sqlite")
            request.state.docs_dir = getattr(settings, "docs_dir", "data/docs")
            request.state.lancedb_uri = getattr(settings, "lancedb_uri", "data/lancedb")
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        if not getattr(settings, "rate_limit_enabled", False):
            return await call_next(request)
        # Determine workspace identifier for scoping
        from uamm.security.auth import parse_bearer, lookup_key

        ws = request.headers.get("X-Workspace") or getattr(
            request.state, "workspace", None
        )
        if not ws:
            token = parse_bearer(request.headers.get("Authorization"))
            if token:
                rec = lookup_key(settings.db_path, token)
                if rec and rec.active:
                    ws = rec.workspace
        if not ws:
            ws = "default"
        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is None:
            limiter = {}
            setattr(request.app.state, "rate_limiter", limiter)
        import time as _t

        # Select per-role or global limit
        role = getattr(request.state, "role", None)
        per_min = None
        if role == "viewer":
            per_min = getattr(settings, "rate_limit_viewer_per_minute", None)
        elif role == "editor":
            per_min = getattr(settings, "rate_limit_editor_per_minute", None)
        elif role == "admin":
            per_min = getattr(settings, "rate_limit_admin_per_minute", None)
        if not per_min:
            per_min = getattr(settings, "rate_limit_per_minute", 120)
        per_min = max(1, int(per_min))
        now = int(_t.time())
        window = now // 60
        state = limiter.get(ws)
        if not state or state.get("window") != window:
            state = {"window": window, "counts": {}}
            limiter[ws] = state
        counts = state["counts"]
        role_key = role or "anonymous"
        counts[role_key] = int(counts.get(role_key, 0)) + 1
        total = sum(int(v or 0) for v in counts.values())
        # Enforce per-role first, then global
        if (
            counts[role_key] > per_min
            or total > getattr(settings, "rate_limit_per_minute", 120) * 10_000
        ):
            from fastapi import HTTPException

            raise HTTPException(status_code=429, detail="rate_limit_exceeded")
        return await call_next(request)


def create_app() -> FastAPI:
    # Define a single lifespan that encapsulates startup/shutdown
    @asynccontextmanager
    async def lifespan(app_obj: FastAPI):
        # STARTUP
        settings = load_settings()
        app.state.settings = settings
        secret_manager = SecretManager.from_settings(settings)
        strict_secrets = str(getattr(settings, "env", "dev")).lower() not in {
            "dev",
            "test",
        }
        try:
            secret_manager.bootstrap(strict=strict_secrets)
        except SecretError as exc:
            logging.getLogger("uamm.security").error(
                "secret_bootstrap_failed", extra={"error": str(exc)}
            )
            raise
        if secret_manager.missing:
            logging.getLogger("uamm.security").warning(
                "secrets_missing",
                extra={
                    "aliases": sorted(secret_manager.missing.keys()),
                    "env": getattr(settings, "env", "dev"),
                },
            )
        app.state.secrets = secret_manager
        ensure_schema(settings.db_path, settings.schema_path)
        ensure_migrations(settings.db_path)
        app.state.idem_store = IdempotencyStore()
        app.state.approvals = ApprovalsStore(
            ttl_seconds=getattr(settings, "approvals_ttl_seconds", 1800)
        )
        app.state.cp_cache = CPThresholdCache()
        app.state.tuner_store = TunerProposalStore(
            ttl_seconds=getattr(settings, "tuner_proposal_ttl_seconds", 3600)
        )
        buckets = {"0.1": 0, "0.5": 0, "1": 0, "2.5": 0, "6": 0, "+Inf": 0}
        app.state.metrics = {
            "requests": 0,
            "answers": 0,
            "abstain": 0,
            "accept": 0,
            "iterate": 0,
            "by_domain": {},
            "answer_latency": {"buckets": dict(buckets), "sum": 0.0, "count": 0},
            "answer_latency_by_domain": {},
            "first_token_latency": {"buckets": dict(buckets), "sum": 0.0, "count": 0},
            "first_token_latency_by_domain": {},
            "alerts": {},
            "approvals": {
                "pending": 0,
                "approved": 0,
                "denied": 0,
                "avg_pending_age": 0.0,
                "max_pending_age": 0.0,
            },
        }
        import asyncio
        import sqlite3 as _sqlite3

        async def _multi_ttl_cleaner():
            steps_ttl_days = int(getattr(settings, "steps_ttl_days", 90))
            memory_ttl_days = int(getattr(settings, "memory_ttl_days", 60))
            interval = int(getattr(settings, "docs_scan_interval_seconds", 60)) or 60
            from datetime import timedelta

            steps_ttl = timedelta(days=steps_ttl_days).total_seconds()
            mem_ttl = timedelta(days=memory_ttl_days).total_seconds()
            while True:
                try:
                    import time as _t

                    now = _t.time()
                    try:
                        con = _sqlite3.connect(settings.db_path)
                        with con:
                            con.execute(
                                "DELETE FROM steps WHERE ts < ?", (now - steps_ttl,)
                            )
                            con.execute(
                                "DELETE FROM memory WHERE ts < ?", (now - mem_ttl,)
                            )
                        con.close()
                    except Exception:
                        pass
                    try:
                        from uamm.memory.promote import (
                            promote_episodic_to_semantic as _prom,
                        )

                        if getattr(settings, "memory_promotion_enabled", False):
                            stats = _prom(
                                settings.db_path,
                                min_support=int(
                                    getattr(settings, "memory_promotion_min_support", 3)
                                    or 3
                                ),
                            )
                            mem = app.state.metrics.setdefault(
                                "memory", {"promotions": 0}
                            )
                            mem["promotions"] = int(mem.get("promotions", 0)) + int(
                                stats.promoted
                            )
                    except Exception:
                        pass
                    try:
                        con = _sqlite3.connect(settings.db_path)
                        con.row_factory = _sqlite3.Row
                        rows = con.execute("SELECT slug FROM workspaces").fetchall()
                        con.close()
                        for r in rows:
                            slug = r["slug"]
                            paths = ws_resolve_paths(settings.db_path, slug, settings)
                            dbp = paths.get("db_path")
                            if not dbp:
                                continue
                            try:
                                c2 = _sqlite3.connect(dbp)
                                with c2:
                                    c2.execute(
                                        "DELETE FROM steps WHERE ts < ?",
                                        (now - steps_ttl,),
                                    )
                                    c2.execute(
                                        "DELETE FROM memory WHERE ts < ?",
                                        (now - mem_ttl,),
                                    )
                                c2.close()
                            except Exception:
                                continue
                    except Exception:
                        pass
                except Exception:
                    pass
                await asyncio.sleep(max(30, interval))

        app.state.ttl_task = asyncio.create_task(_multi_ttl_cleaner())
        if getattr(settings, "docs_auto_ingest", True):

            async def _docs_watcher():
                try:
                    while True:
                        try:
                            from pathlib import Path as _Path

                            try:
                                con = _sqlite3.connect(settings.db_path)
                                con.row_factory = _sqlite3.Row
                                rows = con.execute(
                                    "SELECT slug FROM workspaces"
                                ).fetchall()
                                con.close()
                            except Exception:
                                rows = []
                            if rows:
                                for r in rows:
                                    slug = r["slug"]
                                    paths = ws_resolve_paths(
                                        settings.db_path, slug, settings
                                    )
                                    docs_dir = paths.get("docs_dir")
                                    dbp = paths.get("db_path")
                                    if not docs_dir or not dbp:
                                        continue
                                    try:
                                        if _Path(docs_dir).exists():
                                            scan_folder(
                                                dbp, docs_dir, settings=settings
                                            )
                                    except Exception:
                                        continue
                            else:
                                scan_folder(
                                    settings.db_path,
                                    settings.docs_dir,
                                    settings=settings,
                                )
                        except Exception as exc:
                            logging.getLogger("uamm.rag.ingest").warning(
                                "docs_scan_failed", extra={"error": str(exc)}
                            )
                        await asyncio.sleep(
                            max(5, int(settings.docs_scan_interval_seconds))
                        )
                except Exception:
                    return

            app.state.docs_task = asyncio.create_task(_docs_watcher())

        def _tau_supplier(domain: str = "default"):
            cache = getattr(app.state, "cp_cache", None)
            target = settings.cp_target_mis
            if cache is not None:
                cached = cache.get(domain, target)
                if cached is not None:
                    return cached
            tau = cp_store.compute_threshold(
                settings.db_path, domain=domain, target_mis=target
            )
            if cache is not None:
                stats = cp_store.domain_stats(settings.db_path).get(domain, {})
                cache.set(domain, tau, target, stats)
            return tau

        app.state.cp_tau_supplier = _tau_supplier
        if getattr(settings, "seed_admin_enabled", False):
            try:
                if (
                    count_keys(
                        settings.db_path, workspace=settings.seed_admin_workspace
                    )
                    == 0
                ):
                    if settings.seed_admin_key:
                        insert_api_key(
                            settings.db_path,
                            workspace=settings.seed_admin_workspace,
                            role="admin",
                            label=settings.seed_admin_label,
                            token=settings.seed_admin_key,
                        )
                    elif getattr(settings, "seed_admin_autogen", False):
                        token = new_key(
                            prefix=getattr(settings, "api_key_prefix", "wk_")
                        )
                        insert_api_key(
                            settings.db_path,
                            workspace=settings.seed_admin_workspace,
                            role="admin",
                            label=settings.seed_admin_label,
                            token=token,
                        )
            except Exception as exc:
                logging.getLogger("uamm.auth").error(
                    "seed_admin_failed", extra={"error": str(exc)}
                )
        # Yield control
        try:
            yield
        finally:
            # SHUTDOWN
            task = getattr(app.state, "ttl_task", None)
            if task:
                task.cancel()
            dtask = getattr(app.state, "docs_task", None)
            if dtask:
                dtask.cancel()

    description = (
        "Uncertainty-Aware Agent with Modular Memory (UAMM). "
        "Provides grounded answers with SNNE uncertainty quantification, conformal policies, "
        "tool-assisted refinements, and streaming SSE responses."
    )
    app = FastAPI(
        title="UAMM API",
        version="0.1.0",
        description=description,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(WorkspaceMiddleware)
    app.add_middleware(WorkspacePathMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(api_router)
    return app


# For `uvicorn uamm.api.main:create_app` factory
