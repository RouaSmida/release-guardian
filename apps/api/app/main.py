"""Release Guardian demo API: accepts text-analysis jobs and queues them for the worker."""

import asyncio
import logging
import random
import time
import uuid
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

from app.config import Settings
from app.logging_config import configure_logging
from app.store import JobStore, build_store

log = logging.getLogger("api")

# Probes and scrapes are not user traffic: they are neither measured nor faulted.
UNMEASURED_PATHS = {"/healthz", "/readyz", "/metrics"}
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class JobRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


def create_app(
    settings: Settings | None = None,
    store: JobStore | None = None,
    rng: random.Random | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store if store is not None else build_store(settings.redis_url)
    rng = rng or random.Random()

    # A registry per app instance keeps unit tests isolated from each other.
    registry = CollectorRegistry()
    labels = ["service", "method", "route"]
    requests_total = Counter(
        "http_requests_total", "HTTP requests handled.", [*labels, "status"], registry=registry
    )
    errors_total = Counter(
        "http_request_errors_total", "HTTP requests answered with 5xx.", labels, registry=registry
    )
    duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds.",
        labels,
        buckets=LATENCY_BUCKETS,
        registry=registry,
    )
    Gauge("app_info", "Build information.", ["service", "version"], registry=registry).labels(
        settings.service_name, settings.version
    ).set(1)

    app = FastAPI(title="Release Guardian API", version=settings.version)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        if request.url.path in UNMEASURED_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        fault = None
        if settings.fault_mode == "latency":
            fault = "latency"
            await asyncio.sleep(settings.fault_latency_ms / 1000)
        if settings.fault_mode == "errors" and rng.random() < settings.fault_rate:
            fault = "errors"
            response: Response = JSONResponse({"detail": "injected fault"}, status_code=500)
        else:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"path": request.url.path})
                response = JSONResponse({"detail": "internal error"}, status_code=500)

        elapsed = time.perf_counter() - start
        # Use the route template, not the raw path, to keep label cardinality bounded.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        base = (settings.service_name, request.method, route)
        requests_total.labels(*base, str(response.status_code)).inc()
        duration.labels(*base).observe(elapsed)
        if response.status_code >= 500:
            errors_total.labels(*base).inc()
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed * 1000, 2),
                "fault": fault,
            },
        )
        return response

    @app.get("/healthz")
    def healthz():
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        """Readiness: dependencies are reachable, so traffic can be served."""
        if not store.ping():
            return JSONResponse({"status": "unavailable", "reason": "store"}, status_code=503)
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/info")
    def info():
        return {
            "service": settings.service_name,
            "version": settings.version,
            "fault_mode": settings.fault_mode,
        }

    @app.post("/api/jobs", status_code=202)
    def create_job(body: JobRequest):
        job = {
            "id": uuid.uuid4().hex[:12],
            "text": body.text,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "result": None,
        }
        store.enqueue(job)
        return job

    @app.get("/api/jobs")
    def list_jobs(limit: int = 20):
        return {"jobs": store.recent(max(1, min(limit, 50)))}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    return app


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.service_name, settings.version, settings.log_level)
    log.info("starting", extra={"port": settings.port, "fault_mode": settings.fault_mode})
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - must listen on all interfaces inside the container
        port=settings.port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
