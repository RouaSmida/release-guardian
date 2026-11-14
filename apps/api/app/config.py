"""Runtime configuration, read once from environment variables."""

import os
from dataclasses import dataclass

FAULT_MODES = ("none", "errors", "latency")


@dataclass(frozen=True)
class Settings:
    service_name: str = "api"
    version: str = "dev"
    port: int = 8080
    log_level: str = "INFO"
    redis_url: str = ""  # empty = in-memory store (local development and tests)
    fault_mode: str = "none"
    fault_rate: float = 0.5  # share of requests that fail when fault_mode=errors
    fault_latency_ms: int = 800  # delay added when fault_mode=latency

    @classmethod
    def from_env(cls) -> "Settings":
        fault_mode = os.getenv("FAULT_MODE", "none").strip().lower()
        if fault_mode not in FAULT_MODES:
            raise ValueError(f"FAULT_MODE must be one of {FAULT_MODES}, got {fault_mode!r}")
        return cls(
            service_name=os.getenv("SERVICE_NAME", "api"),
            version=os.getenv("APP_VERSION", "dev"),
            port=int(os.getenv("PORT", "8080")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            redis_url=os.getenv("REDIS_URL", ""),
            fault_mode=fault_mode,
            fault_rate=float(os.getenv("FAULT_RATE", "0.5")),
            fault_latency_ms=int(os.getenv("FAULT_LATENCY_MS", "800")),
        )
