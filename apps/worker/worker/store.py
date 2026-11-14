"""Worker side of the job queue (the API writes, the worker reads and updates)."""

import json
from collections import deque
from typing import Protocol

QUEUE_KEY = "jobs:queue"
JOB_TTL_SECONDS = 3600


class JobQueue(Protocol):
    def ping(self) -> bool: ...
    def dequeue(self, timeout: int) -> dict | None: ...
    def save(self, job: dict) -> None: ...


class InMemoryQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: deque[str] = deque()

    def put(self, job: dict) -> None:
        self.jobs[job["id"]] = job
        self.queue.append(job["id"])

    def ping(self) -> bool:
        return True

    def dequeue(self, timeout: int) -> dict | None:
        return self.jobs[self.queue.popleft()] if self.queue else None

    def save(self, job: dict) -> None:
        self.jobs[job["id"]] = job


class RedisQueue:
    def __init__(self, url: str) -> None:
        import redis  # imported here so tests do not need a Redis client

        self._redis = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def dequeue(self, timeout: int) -> dict | None:
        item = self._redis.blpop([QUEUE_KEY], timeout=timeout)
        if item is None:
            return None
        raw = self._redis.get(f"job:{item[1]}")
        return json.loads(raw) if raw else None  # job may have expired

    def save(self, job: dict) -> None:
        self._redis.set(f"job:{job['id']}", json.dumps(job), ex=JOB_TTL_SECONDS)


def build_queue(redis_url: str) -> JobQueue:
    return RedisQueue(redis_url) if redis_url else InMemoryQueue()
