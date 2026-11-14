import numpy as np
import pytest
from prometheus_client import CollectorRegistry

from detector.config import Settings
from detector.main import Detector
from detector.prometheus import error_rate_query
from detector.scoring import anomaly_score

NOISE = np.array([0.005, 0.02])
THRESHOLD = 0.7  # same default as the Helm chart's AnalysisTemplate


def flat_baseline(n: int = 60) -> np.ndarray:
    """A healthy stable version: no errors, constant 20 ms p95."""
    return np.tile([0.0, 0.02], (n, 1))


def noisy_baseline(n: int = 120) -> np.ndarray:
    rng = np.random.default_rng(7)
    return np.column_stack([rng.uniform(0, 0.01, n), rng.normal(0.05, 0.01, n)])


@pytest.mark.parametrize("baseline", [flat_baseline(), noisy_baseline(), flat_baseline(8)])
def test_healthy_canary_scores_below_threshold(baseline):
    canary = np.median(baseline, axis=0) + [0.001, 0.003]
    assert anomaly_score(baseline, canary, NOISE) < 0.6


@pytest.mark.parametrize("baseline", [flat_baseline(), noisy_baseline(), flat_baseline(8)])
@pytest.mark.parametrize("canary", [[0.5, 0.02], [0.0, 0.85], [0.08, 0.02], [0.5, 0.85]])
def test_degraded_canary_scores_above_threshold(baseline, canary):
    assert anomaly_score(baseline, np.array(canary), NOISE) > THRESHOLD


def test_better_than_baseline_is_not_anomalous():
    assert anomaly_score(noisy_baseline(), np.array([0.0, 0.001]), NOISE) < 0.6


def test_score_is_deterministic():
    args = (noisy_baseline(), np.array([0.3, 0.4]), NOISE)
    assert anomaly_score(*args) == anomaly_score(*args)


class FakePrometheus:
    """Answers queries based on whether they target the canary or the stable pods."""

    def __init__(self, canary_errors, canary_p95, baseline_points: int = 40):
        self.canary = {"errors": canary_errors, "duration": canary_p95}
        self.baseline_points = baseline_points
        self.fail = False

    @staticmethod
    def _kind(query: str) -> str:
        return "duration" if "duration_seconds_bucket" in query else "errors"

    def instant(self, query):
        if self.fail:
            raise ConnectionError("prometheus down")
        assert 'rollout_role="canary"' in query
        return self.canary[self._kind(query)]

    def series(self, query, minutes, step_seconds):
        assert 'rollout_role="stable"' in query
        value = 0.02 if self._kind(query) == "duration" else 0.0
        return {float(ts): value for ts in range(self.baseline_points)}


def detector_for(client) -> tuple[Detector, CollectorRegistry]:
    registry = CollectorRegistry()
    return Detector(Settings(services=("api",)), client, registry), registry


def test_bad_canary_is_published_as_high_score():
    detector, registry = detector_for(FakePrometheus(0.5, 0.03))
    (result,) = detector.run_cycle()
    assert result.reason == "scored"
    assert registry.get_sample_value("release_anomaly_score", {"service": "api"}) > THRESHOLD
    assert registry.get_sample_value("release_anomaly_baseline_samples", {"service": "api"}) == 40
    assert detector.healthy


def test_no_canary_scores_zero():
    detector, registry = detector_for(FakePrometheus(None, None))
    assert detector.run_cycle()[0].reason == "no_canary"
    assert registry.get_sample_value("release_anomaly_score", {"service": "api"}) == 0


def test_short_baseline_scores_zero():
    detector, _ = detector_for(FakePrometheus(0.5, 0.9, baseline_points=3))
    result = detector.run_cycle()[0]
    assert (result.reason, result.score) == ("insufficient_baseline", 0.0)


def test_prometheus_failure_marks_detector_unhealthy():
    client = FakePrometheus(0.0, 0.02)
    detector, registry = detector_for(client)
    client.fail = True
    assert detector.run_cycle() == []
    assert not detector.healthy
    assert registry.get_sample_value("release_anomaly_evaluation_failures_total") == 1


def test_error_rate_query_treats_a_missing_error_series_as_zero_errors():
    query = error_rate_query("ns", "api", "canary", "1m")
    numerator, denominator = query.split(" / ")
    assert "or on() vector(0)" in numerator  # no error series = 0 errors
    assert "vector(0)" not in denominator  # no request series = no data
