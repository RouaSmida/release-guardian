import json
import random
import urllib.error
import urllib.request

import pytest
from prometheus_client import CollectorRegistry

from worker.config import Settings
from worker.health import start_health_server
from worker.processor import Processor, analyse_text
from worker.store import InMemoryQueue


def make(**overrides):
    registry = CollectorRegistry()
    queue = InMemoryQueue()
    slept: list[float] = []
    processor = Processor(Settings(**overrides), queue, registry, random.Random(1), slept.append)
    return processor, queue, registry, slept


def job(text: str = "the quick brown fox jumps over the lazy dog") -> dict:
    return {"id": "abc123", "text": text, "status": "queued", "result": None}


def test_analyse_text():
    result = analyse_text("The quick the QUICK fox")
    assert result["words"] == 5
    assert result["unique_words"] == 3
    assert result["longest_word"] == "quick"
    assert analyse_text("")["words"] == 0


def test_job_is_processed_and_saved():
    processor, queue, registry, _ = make()
    queue.put(job())
    assert processor.run_once() is True
    saved = queue.jobs["abc123"]
    assert saved["status"] == "done"
    assert saved["result"]["words"] == 9
    assert (
        registry.get_sample_value("worker_jobs_total", {"service": "worker", "status": "done"}) == 1
    )
    assert (
        registry.get_sample_value("worker_job_duration_seconds_count", {"service": "worker"}) == 1
    )


def test_empty_queue_returns_false():
    processor, *_ = make()
    assert processor.run_once() is False


def test_fault_mode_errors_fails_jobs():
    processor, queue, registry, _ = make(fault_mode="errors", fault_rate=1.0)
    result = processor.process(job())
    assert result["status"] == "failed"
    assert queue.jobs["abc123"]["error"] == "injected fault"
    assert registry.get_sample_value("worker_job_errors_total", {"service": "worker"}) == 1


def test_fault_mode_latency_delays_jobs():
    processor, _, _, slept = make(fault_mode="latency", fault_latency_ms=250)
    assert processor.process(job())["status"] == "done"
    assert slept == [0.25]


def test_health_server_endpoints():
    registry = CollectorRegistry()
    ready = {"value": True}
    server = start_health_server(0, registry, lambda: ready["value"])
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/healthz") as response:
            assert json.load(response) == {"status": "ok"}
        assert urllib.request.urlopen(f"{base}/readyz").status == 200
        assert urllib.request.urlopen(f"{base}/metrics").status == 200
        ready["value"] = False
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/readyz")
        assert error.value.code == 503
    finally:
        server.shutdown()
