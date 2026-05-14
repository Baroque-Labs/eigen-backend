"""arq tasks. Define here; register in eigen/worker.py.

Tasks must be re-entrant; arq will retry on failure with exponential backoff.
"""
import logging

from eigen import models
from eigen.db import SessionLocal
from eigen.esp import get_dispatcher

log = logging.getLogger("eigen.tasks")


async def dispatch_send(ctx, send_id: int, to: str, subject: str, html: str) -> dict:
    """Idempotent: if the Send already has a provider_message_id, skip."""
    db = SessionLocal()
    try:
        s = db.get(models.Send, send_id)
        if s is None:
            log.warning("dispatch_send: send_id=%s not found", send_id)
            return {"ok": False, "reason": "missing"}
        if s.provider_message_id:
            return {"ok": True, "skipped": True, "provider_message_id": s.provider_message_id}
        result = get_dispatcher().send(
            to=to, subject=subject, html=html, headers={"X-Eigen-Send-Id": str(send_id)}
        )
        s.provider = result.provider
        s.provider_message_id = result.provider_message_id
        db.commit()
        return {"ok": True, "provider_message_id": result.provider_message_id}
    finally:
        db.close()
