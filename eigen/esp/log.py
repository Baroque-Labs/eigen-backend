import logging
import uuid

from eigen.esp import SendResult

log = logging.getLogger("eigen.esp.log")


class LogDispatcher:
    name = "log"

    def send(self, *, to: str, subject: str, html: str, headers: dict[str, str] | None = None) -> SendResult:
        h = headers or {}
        mid = h.get("X-Eigen-Provider-Message-Id") or str(uuid.uuid4())
        log.info("DISPATCH provider=log mid=%s to=%s subject=%r", mid, to, subject)
        return SendResult(provider=self.name, provider_message_id=mid)
