"""Minimal Prometheus HTTP API client and the PromQL used by the detector."""

import math
import time

import requests


def error_rate_query(namespace: str, service: str, role: str, window: str) -> str:
    selector = f'namespace="{namespace}",service="{service}",rollout_role="{role}"'
    # The error counter only exists once a first error happened, so "no series"
    # means "no errors" (0). The request counter decides whether there is data.
    return (
        f"(sum(rate(http_request_errors_total{{{selector}}}[{window}])) or on() vector(0))"
        f" / sum(rate(http_requests_total{{{selector}}}[{window}]))"
    )


def p95_latency_query(namespace: str, service: str, role: str, window: str) -> str:
    selector = f'namespace="{namespace}",service="{service}",rollout_role="{role}"'
    return (
        "histogram_quantile(0.95, sum by (le) "
        f"(rate(http_request_duration_seconds_bucket{{{selector}}}[{window}])))"
    )


def _finite(value: str) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def _get(self, path: str, params: dict) -> list:
        response = requests.get(f"{self._base_url}{path}", params=params, timeout=self._timeout)
        response.raise_for_status()
        return response.json()["data"]["result"]

    def instant(self, query: str) -> float | None:
        """Current value of a single-series query; None when there is no data."""
        result = self._get("/api/v1/query", {"query": query})
        return _finite(result[0]["value"][1]) if result else None

    def series(self, query: str, minutes: int, step_seconds: int) -> dict[float, float]:
        """Recent values of a single-series query as {timestamp: value}.

        The window ends on a multiple of the step, so two queries made a moment
        apart return the same timestamps and can be joined on them.
        """
        end = time.time() // step_seconds * step_seconds
        params = {"query": query, "start": end - minutes * 60, "end": end, "step": step_seconds}
        result = self._get("/api/v1/query_range", params)
        if not result:
            return {}
        points = ((float(ts), _finite(value)) for ts, value in result[0]["values"])
        return {ts: value for ts, value in points if value is not None}
