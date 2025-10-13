import logging
import time
import uuid
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
from uamm.storage.ttl import ttl_cleaner
from uamm.policy import cp_store
from uamm.security.secrets import SecretManager, SecretError


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


def create_app() -> FastAPI:
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
    )
    app.add_middleware(RequestIDMiddleware)

    @app.on_event("startup")
    async def _startup() -> None:
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
        # initialize metrics
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
        # launch TTL cleaner
        import asyncio

        app.state.ttl_task = asyncio.create_task(
            ttl_cleaner(
                settings.db_path,
                steps_ttl_days=settings.steps_ttl_days,
                memory_ttl_days=settings.memory_ttl_days,
            )
        )

        # CP threshold supplier for default domain
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

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "ttl_task", None)
        if task:
            task.cancel()

    app.include_router(api_router)
    return app


# For `uvicorn uamm.api.main:create_app` factory
