import logging

import resend

from eigen.config import settings
from eigen.esp import SendResult

log = logging.getLogger("eigen.esp.resend")


class ResendDispatcher:
    name = "resend"

    def __init__(self) -> None:
        if not settings().resend_api_key:
            raise RuntimeError("EIGEN_RESEND_API_KEY not set")
        resend.api_key = settings().resend_api_key

    def send(self, *, to: str, subject: str, html: str, headers: dict[str, str] | None = None) -> SendResult:
        params = {
            "from": settings().sender_from,
            "to": [to],
            "subject": subject,
            "html": html or f"<p>{subject}</p>",
        }
        if headers:
            params["headers"] = headers
        r = resend.Emails.send(params)
        return SendResult(provider=self.name, provider_message_id=r["id"])
