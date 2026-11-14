"""Runtime configuration, read once from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    service_name: str = "anomaly-detector"
    version: str = "dev"
    port: int = 8080
    log_level: str = "INFO"
    prometheus_url: str = "http://prometheus-operated.monitoring.svc:9090"
    namespace: str = "release-guardian"
    services: tuple[str, ...] = ("web", "api")  # services whose canaries are scored
    interval_seconds: int = 30
    rate_window: str = "1m"
    baseline_window_minutes: int = 30
    baseline_step_seconds: int = 15
    min_baseline_samples: int = 8
    # Differences smaller than this are treated as noise, not as anomalies.
    error_rate_noise: float = 0.005  # 0.5 percentage points
    latency_noise_seconds: float = 0.02  # 20 ms

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.getenv
        return cls(
            version=env("APP_VERSION", "dev"),
            port=int(env("PORT", "8080")),
            log_level=env("LOG_LEVEL", "INFO").upper(),
            prometheus_url=env("PROMETHEUS_URL", cls.prometheus_url).rstrip("/"),
            namespace=env("TARGET_NAMESPACE", cls.namespace),
            services=tuple(s.strip() for s in env("SERVICES", "web,api").split(",") if s.strip()),
            interval_seconds=int(env("INTERVAL_SECONDS", "30")),
            rate_window=env("RATE_WINDOW", "1m"),
            baseline_window_minutes=int(env("BASELINE_WINDOW_MINUTES", "30")),
            baseline_step_seconds=int(env("BASELINE_STEP_SECONDS", "15")),
            min_baseline_samples=int(env("MIN_BASELINE_SAMPLES", "8")),
            error_rate_noise=float(env("ERROR_RATE_NOISE", "0.005")),
            latency_noise_seconds=float(env("LATENCY_NOISE_SECONDS", "0.02")),
        )
