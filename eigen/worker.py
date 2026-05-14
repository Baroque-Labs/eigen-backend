"""arq WorkerSettings.

Run with:
    arq eigen.worker.WorkerSettings
"""
from arq.connections import RedisSettings

from eigen.config import settings
from eigen.tasks import dispatch_send


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings().redis_url)


class WorkerSettings:
    functions = [dispatch_send]
    redis_settings = _redis_settings()
    max_jobs = 50
    job_timeout = 30
    keep_result = 60
