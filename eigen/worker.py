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
        cron(cron_tick_campaigns, second=0),  # every minute on :00
        cron(cron_settle_campaigns, second=30),  # every minute on :30
        cron(cron_research_campaigns, minute=set(range(0, 60, 5))),  # every 5 min
    ]
    redis_settings = _redis_settings()
    max_jobs = 50
    job_timeout = 60
    keep_result = 60
