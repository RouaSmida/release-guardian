import random

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import InMemoryStore


def client_for(**overrides) -> TestClient:
    return TestClient(create_app(Settings(**overrides), InMemoryStore(), random.Random(1)))


def metric(client: TestClient, name: str) -> float:
    """Sum every sample of a metric family from the /metrics text output."""
    total = 0.0
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            total += float(line.rsplit(" ", 1)[1])
    return total


def test_health_and_readiness():
    client = client_for()
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200


def test_readiness_fails_when_store_is_down():
    store = InMemoryStore()
    store.ping = lambda: False
    client = TestClient(create_app(Settings(), store))
    assert client.get("/readyz").status_code == 503
    assert client.get("/healthz").status_code == 200  # liveness is independent


def test_job_lifecycle():
    client = client_for()
    created = client.post("/api/jobs", json={"text": "hello release guardian"})
    assert created.status_code == 202
    job = created.json()
    assert job["status"] == "queued"
    assert client.get(f"/api/jobs/{job['id']}").json() == job
    assert client.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]


def test_validation_and_not_found():
    client = client_for()
    assert client.post("/api/jobs", json={"text": ""}).status_code == 422
    assert client.get("/api/jobs/missing").status_code == 404


def test_metrics_count_requests_but_not_probes():
    client = client_for()
    client.get("/api/info")
    client.get("/healthz")
    assert metric(client, "http_requests_total") == 1
    assert metric(client, "http_request_errors_total") == 0
    assert metric(client, "http_request_duration_seconds_count") == 1


def test_fault_mode_errors_fails_requests_but_not_probes():
    client = client_for(fault_mode="errors", fault_rate=1.0)
    assert client.get("/api/info").status_code == 500
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert metric(client, "http_request_errors_total") == 1


def test_fault_mode_latency_delays_requests():
    client = client_for(fault_mode="latency", fault_latency_ms=60)
    assert client.get("/api/info").status_code == 200
    assert metric(client, "http_request_duration_seconds_sum") >= 0.06


def test_invalid_fault_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("FAULT_MODE", "chaos")
    with pytest.raises(ValueError):
        Settings.from_env()
