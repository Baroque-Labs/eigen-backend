"""Smoke-screened dispatcher: pretends to send email."""
import logging

log = logging.getLogger("eigen.dispatcher")


def dispatch(send_id: int, recipient_email: str, subject: str, body: str) -> None:
    log.info("DISPATCH send=%s to=%s subject=%r", send_id, recipient_email, subject)
