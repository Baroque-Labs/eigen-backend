"""ESP adapter that delivers to eigen-inbox.

The inbox /send expects rich context (campaign_id, variant_id, org_id,
cohort, true_ctr). We pull those from headers prefixed with X-Eigen-* which
the dispatch site already adds.
"""
import logging
import uuid

import httpx

from eigen.config import settings
from eigen.esp import SendResult

log = logging.getLogger("eigen.esp.inbox")


class InboxDispatcher:
    name = "inbox"

    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        headers: dict[str, str] | None = None,
    ) -> SendResult:
        headers = headers or {}
        # Backend pre-assigns provider_message_id and stores it on the Send
        # row before calling us. Inbox MUST honor it so webhook callbacks can
        # find the row.
        msg_id = headers.get("X-Eigen-Provider-Message-Id") or f"inbox_{uuid.uuid4().hex}"
        try:
            payload = {
                "backend_send_id": int(headers.get("X-Eigen-Send-Id", 0)),
                "backend_campaign_id": int(headers.get("X-Eigen-Campaign-Id", 0)),
                "backend_variant_id": int(headers.get("X-Eigen-Variant-Id", 0)),
                "backend_org_id": int(headers.get("X-Eigen-Org-Id", 0)),
                "recipient": to,
                "cohort": headers.get("X-Eigen-Cohort", "default"),
                "subject": subject,
                "body": html or "",
                "true_ctr": float(headers.get("X-Eigen-True-Ctr", "0.05")),
                "provider_message_id": msg_id,
            }
        except (TypeError, ValueError) as e:
            log.error("invalid headers for inbox dispatch: %s headers=%s", e, headers)
            return SendResult(provider=self.name, provider_message_id=msg_id)

        try:
            r = httpx.post(f"{settings().inbox_url}/send", json=payload, timeout=5.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("inbox /send failed (%s), dropping email on the floor", e)
        return SendResult(provider=self.name, provider_message_id=msg_id)
