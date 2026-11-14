"""Release Guardian demo worker: consumes jobs from the queue and stores results."""

import logging
import signal
import threading
import time

from prometheus_client import CollectorRegistry

from worker.config import Settings
from worker.health import start_health_server
from worker.logging_config import configure_logging
from worker.processor import Processor
from worker.store import build_queue

log = logging.getLogger("worker")


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.service_name, settings.version, settings.log_level)
    registry = CollectorRegistry()
    queue = build_queue(settings.redis_url)
    processor = Processor(settings, queue, registry)
    server = start_health_server(settings.port, registry, queue.ping)

    stop = threading.Event()
    # Kubernetes sends SIGTERM first: finish the current job, then exit cleanly.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("starting", extra={"port": settings.port, "fault_mode": settings.fault_mode})
    while not stop.is_set():
        try:
            if not processor.run_once() and not settings.redis_url:
                time.sleep(0.5)  # the in-memory queue does not block
        except Exception:
            log.exception("queue error, retrying")
            stop.wait(2)
    server.shutdown()
    log.info("stopped")


if __name__ == "__main__":
    main()
