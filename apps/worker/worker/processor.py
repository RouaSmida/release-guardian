"""Job processing: the business logic plus metrics and fault injection."""

import hashlib
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from worker.config import Settings
from worker.store import JobQueue

log = logging.getLogger("worker")

DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def analyse_text(text: str) -> dict:
    """The actual 'work': simple, deterministic text statistics."""
    words = text.split()
    return {
        "characters": len(text),
        "words": len(words),
        "unique_words": len({word.lower() for word in words}),
        "longest_word": max(words, key=len) if words else "",
        "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
    }


class InjectedFault(RuntimeError):
    pass


class Processor:
    def __init__(
        self,
        settings: Settings,
        queue: JobQueue,
        registry: CollectorRegistry,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self._rng = rng or random.Random()
        self._sleep = sleep
        name = settings.service_name
        self._jobs = Counter(
            "worker_jobs_total", "Jobs processed.", ["service", "status"], registry=registry
        )
        self._errors = Counter(
            "worker_job_errors_total", "Jobs that failed.", ["service"], registry=registry
        ).labels(name)
        self._duration = Histogram(
            "worker_job_duration_seconds",
            "Job processing duration in seconds.",
            ["service"],
            buckets=DURATION_BUCKETS,
            registry=registry,
        ).labels(name)
        Gauge("app_info", "Build information.", ["service", "version"], registry=registry).labels(
            name, settings.version
        ).set(1)

    def process(self, job: dict) -> dict:
        """Process one job, record the outcome, and persist it."""
        start = time.perf_counter()
        try:
            if self.settings.fault_mode == "latency":
                self._sleep(self.settings.fault_latency_ms / 1000)
            if self.settings.fault_mode == "errors" and self._rng.random() < (
                self.settings.fault_rate
            ):
                raise InjectedFault("injected fault")
            job["result"] = analyse_text(job["text"])
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            self._errors.inc()
        elapsed = time.perf_counter() - start
        job["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        job["processed_by"] = self.settings.version
        self.queue.save(job)
        self._jobs.labels(self.settings.service_name, job["status"]).inc()
        self._duration.observe(elapsed)
        log.info(
            "job processed",
            extra={
                "job_id": job["id"],
                "status": job["status"],
                "duration_ms": round(elapsed * 1000, 2),
            },
        )
        return job

    def run_once(self) -> bool:
        """Wait for one job and process it. Returns False when the queue was empty."""
        job = self.queue.dequeue(self.settings.poll_timeout_seconds)
        if job is None:
            return False
        self.process(job)
        return True
