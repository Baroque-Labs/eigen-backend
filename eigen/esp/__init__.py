"""ESP (Email Service Provider) adapters.

A `Dispatcher` knows how to send an email and produces a stable `provider_message_id`
that subsequent webhooks reference back to. All adapters return `SendResult`.
"""
from dataclasses import dataclass
from typing import Protocol

from eigen.config import settings


@dataclass
class SendResult:
    provider: str
    provider_message_id: str


class Dispatcher(Protocol):
    name: str

    def send(self, *, to: str, subject: str, html: str, headers: dict[str, str] | None = None) -> SendResult: ...


def get_dispatcher() -> Dispatcher:
    name = settings().esp
    if name == "log":
        from eigen.esp.log import LogDispatcher

        return LogDispatcher()
    if name == "resend":
        from eigen.esp.resend_adapter import ResendDispatcher

        return ResendDispatcher()
    if name == "fake":
        from eigen.esp.fake import FakeDispatcher

        return FakeDispatcher.get()
    raise ValueError(f"unknown ESP: {name!r}")
