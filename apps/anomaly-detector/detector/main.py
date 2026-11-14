"""Anomaly detector: scores each canary against its stable baseline every 30 s."""

import logging
import signal
import threading
from dataclasses import dataclass

import numpy as np
from prometheus_client import CollectorRegistry, Counter, Gauge

from detector.config import Settings
from detector.health import start_health_server
from detector.logging_config import configure_logging
from detector.prometheus import PrometheusClient, error_rate_query, p95_latency_query
from detector.scoring import anomaly_score

log = logging.getLogger("anomaly-detector")


@dataclass(frozen=True)
class Evaluation:
    service: str
    score: float
    reason: str  # "scored", "no_canary" or "insufficient_baseline"
    baseline_samples: int = 0
    canary_error_rate: float = 0.0
    canary_p95_seconds: float = 0.0


class Detector:
    def __init__(self, settings: Settings, client, registry: CollectorRegistry) -> None:
        self.settings = settings
        self.client = client
        self._score = Gauge(
            "release_anomaly_score",
            "Isolation Forest anomaly score of the canary (about 0.5 = normal, 1 = anomalous). "
            "0 when no canary is running or the baseline is too short.",
            ["service"],
            registry=registry,
        )
        self._samples = Gauge(
            "release_anomaly_baseline_samples",
            "Number of stable-version samples used as baseline.",
            ["service"],
            registry=registry,
        )
        self._canary_error_rate = Gauge(
            "release_anomaly_canary_error_rate",
            "Canary error rate fed to the model.",
            ["service"],
            registry=registry,
        )
        self._canary_p95 = Gauge(
            "release_anomaly_canary_p95_seconds",
            "Canary p95 latency fed to the model.",
            ["service"],
            registry=registry,
        )
        self._failures = Counter(
            "release_anomaly_evaluation_failures_total",
            "Evaluation cycles that failed (for example Prometheus unreachable).",
            registry=registry,
        )
        self.healthy = False

    def evaluate(self, service: str) -> Evaluation:
        s = self.settings
        canary_errors = self.client.instant(
            error_rate_query(s.namespace, service, "canary", s.rate_window)
        )
        canary_p95 = self.client.instant(
            p95_latency_query(s.namespace, service, "canary", s.rate_window)
        )
        if canary_errors is None or canary_p95 is None:
            return Evaluation(service, 0.0, "no_canary")

        window = (s.baseline_window_minutes, s.baseline_step_seconds)
        errors = self.client.series(
            error_rate_query(s.namespace, service, "stable", s.rate_window), *window
        )
        p95 = self.client.series(
            p95_latency_query(s.namespace, service, "stable", s.rate_window), *window
        )
        timestamps = sorted(errors.keys() & p95.keys())
        if len(timestamps) < s.min_baseline_samples:
            return Evaluation(service, 0.0, "insufficient_baseline", len(timestamps))

        baseline = np.array([[errors[ts], p95[ts]] for ts in timestamps])
        score = anomaly_score(
            baseline,
            np.array([canary_errors, canary_p95]),
            np.array([s.error_rate_noise, s.latency_noise_seconds]),
        )
        return Evaluation(service, score, "scored", len(timestamps), canary_errors, canary_p95)

    def run_cycle(self) -> list[Evaluation]:
        """Evaluate every configured service once and publish the results."""
        results = []
        try:
            for service in self.settings.services:
                result = self.evaluate(service)
                self._score.labels(service).set(result.score)
                self._samples.labels(service).set(result.baseline_samples)
                self._canary_error_rate.labels(service).set(result.canary_error_rate)
                self._canary_p95.labels(service).set(result.canary_p95_seconds)
                log.info("evaluation", extra=result.__dict__)
                results.append(result)
            self.healthy = True
        except Exception:
            self.healthy = False
            self._failures.inc()
            log.exception("evaluation failed")
        return results


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.service_name, settings.version, settings.log_level)
    registry = CollectorRegistry()
    detector = Detector(settings, PrometheusClient(settings.prometheus_url), registry)
    server = start_health_server(settings.port, registry, lambda: detector.healthy)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("starting", extra={"prometheus": settings.prometheus_url})
    while not stop.is_set():
        detector.run_cycle()
        stop.wait(settings.interval_seconds)
    server.shutdown()


if __name__ == "__main__":
    main()
