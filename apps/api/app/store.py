"""Job storage and queue.

`RedisStore` is used in the cluster. `InMemoryStore` implements the same
interface so the API can run alone (local development, unit tests).
"""

import json
from collections import deque
from typing import Protocol

QUEUE_KEY = "jobs:queue"
RECENT_KEY = "jobs:recent"
JOB_TTL_SECONDS = 3600
RECENT_LIMIT = 50


class JobStore(Protocol):
    def ping(self) -> bool: ...
    def enqueue(self, job: dict) -> None: ...
    def get(self, job_id: str) -> dict | None: ...
    def recent(self, limit: int = 20) -> list[dict]: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: deque[str] = deque()

    def ping(self) -> bool:
        return True

    def enqueue(self, job: dict) -> None:
        self.jobs[job["id"]] = job
        self.queue.append(job["id"])

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[dict]:
        return list(reversed(list(self.jobs.values())))[:limit]


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis  # imported here so tests do not need a Redis client

        self._redis = redis.Redis.from_url(
            url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2
        )

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def enqueue(self, job: dict) -> None:
        pipe = self._redis.pipeline()
        pipe.set(f"job:{job['id']}", json.dumps(job), ex=JOB_TTL_SECONDS)
        pipe.lpush(RECENT_KEY, job["id"])
        pipe.ltrim(RECENT_KEY, 0, RECENT_LIMIT - 1)
        pipe.rpush(QUEUE_KEY, job["id"])
        pipe.execute()

    def get(self, job_id: str) -> dict | None:
        raw = self._redis.get(f"job:{job_id}")
        return json.loads(raw) if raw else None

    def recent(self, limit: int = 20) -> list[dict]:
        ids = self._redis.lrange(RECENT_KEY, 0, limit - 1)
        if not ids:
            return []
        raws = self._redis.mget([f"job:{job_id}" for job_id in ids])
        return [json.loads(raw) for raw in raws if raw]


def build_store(redis_url: str) -> JobStore:
    return RedisStore(redis_url) if redis_url else InMemoryStore()
