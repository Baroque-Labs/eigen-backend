"""In-process fake ESP for tests. Records every send and lets the test trigger webhooks."""
import threading
import uuid
from dataclasses import dataclass, field

from eigen.esp import SendResult


@dataclass
class RecordedSend:
    provider_message_id: str
    to: str
    subject: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)


class FakeDispatcher:
    """Singleton — keeps state across requests within a single process."""

    name = "fake"
    _instance: "FakeDispatcher | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.sends: list[RecordedSend] = []

    @classmethod
    def get(cls) -> "FakeDispatcher":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def send(self, *, to: str, subject: str, html: str, headers: dict[str, str] | None = None) -> SendResult:
        h = dict(headers or {})
        mid = h.get("X-Eigen-Provider-Message-Id") or f"fake_{uuid.uuid4().hex}"
        self.sends.append(
            RecordedSend(provider_message_id=mid, to=to, subject=subject, html=html, headers=h)
        )
        return SendResult(provider=self.name, provider_message_id=mid)
