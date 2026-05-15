"""arq WorkerSettings.

Run with:
    arq eigen.worker.WorkerSettings
"""
from arq import cron
from arq.connections import RedisSettings

from eigen.config import settings
from eigen.scheduler import cron_research_campaigns, cron_settle_campaigns, cron_tick_campaigns
from eigen.tasks import dispatch_send


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings().redis_url)


class WorkerSettings:
    functions = [dispatch_send]
    cron_jobs = [
        # Every 10 wall-seconds. Each cron-firing consults each campaign's
        # cadence_minutes + calendar + last_tick_at to decide whether to
        # actually dispatch. Granular tick + EIGEN_TIME_SCALE means a
        # cadence_minutes=60 campaign at TIME_SCALE=60 fires every ~60s of
        # wall-clock; at TIME_SCALE=1 it fires every ~60 minutes of wall.
        cron(cron_tick_campaigns, second=set(range(0, 60, 10))),
        cron(cron_settle_campaigns, second=set(range(5, 60, 10))),  # offset 5s
        cron(cron_research_campaigns, minute=set(range(0, 60, 5))),  # every 5 min
    ]
    redis_settings = _redis_settings()
    max_jobs = 50
    job_timeout = 60
    keep_result = 60
