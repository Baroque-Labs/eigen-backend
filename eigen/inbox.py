"""Developer inbox: an HTML view of every dispatched email with a checkbox
per row. Toggle to fire a click event back into the same backend.

Mounted only when EIGEN_ESP is in {fake, log} — never when sending through
Resend, since this view is unauthed and would leak production sends.

The click handler is idempotent on (provider='inbox', send_id) so toggling
the same box twice doesn't double-count.
"""
import html
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from eigen import models
from eigen.bandit import get_or_create_posterior
from eigen.config import settings
from eigen.db import get_db
from eigen.models import utcnow

router = APIRouter(prefix="/_inbox", tags=["inbox"])


def _enabled() -> bool:
    return settings().esp in ("fake", "log")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #fafaf8;
  color: #111;
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
}
header {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 20px 32px; border-bottom: 1px solid #e5e5e5; background: #fff;
}
header h1 { margin: 0; font: 400 28px/1 Georgia, "Instrument Serif", serif; letter-spacing: -0.02em; }
header .meta { font: 11px/1 ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.14em; color: #888; }
header .actions form { display: inline; }
header button, header select {
  font: 11px/1.4 ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.12em;
  padding: 6px 10px; background: #000; color: #fff; border: 1px solid #000; cursor: pointer;
}
header select { background: #fff; color: #111; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #efefef; vertical-align: top; }
th { font: 10px/1 ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.14em; color: #888; padding-top: 14px; padding-bottom: 14px; background: #fafaf8; position: sticky; top: 0; }
td.mono { font-family: ui-monospace, monospace; font-size: 12px; color: #555; }
td.subject { font-family: Georgia, "Instrument Serif", serif; font-size: 16px; }
tr.clicked td { background: #f3fbf3; }
tr.settled-fail td { color: #aaa; }
tr.settled-fail td.subject { font-style: italic; }
.check { width: 22px; height: 22px; cursor: pointer; }
.cohort { font: 10px/1.4 ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.1em; color: #777; }
.empty { padding: 60px; text-align: center; color: #888; font-family: Georgia, serif; font-size: 22px; }
footer { padding: 16px 32px; color: #aaa; font: 10px/1.4 ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.12em; }
form.cell { margin: 0; }
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="3">
<title>Eigen — Inbox ({n_total})</title>
<style>{css}</style>
</head><body>
<header>
  <div>
    <h1>Inbox</h1>
    <div class="meta">{n_total} sends · {n_clicked} clicked · {n_unsettled} unsettled</div>
  </div>
  <div class="actions">
    <form method="get" action="/_inbox" style="display:inline">
      Campaign:
      <select name="campaign_id" onchange="this.form.submit()">
        <option value="">all</option>
        {campaign_options}
      </select>
    </form>
    <form method="post" action="/_inbox/settle{settle_query}">
      <button type="submit" title="Mark all un-clicked sends as failures (β += 1)">Settle unclicked</button>
    </form>
  </div>
</header>
{table}
<footer>Auto-refresh every 3s · this view is dev-only ({esp_mode})</footer>
</body></html>"""


def _build_table(rows: list, has_filter: bool) -> str:
    if not rows:
        return '<div class="empty">No sends yet.</div>'
    head = (
        "<table><thead><tr>"
        "<th>Click</th><th>Subject</th><th>Recipient</th><th>Cohort</th><th>Campaign</th><th>Sent</th>"
        "</tr></thead><tbody>"
    )
    body = []
    for r in rows:
        clicked = r["clicked"]
        settled_fail = r["settled_at"] is not None and not clicked
        cls = "clicked" if clicked else ("settled-fail" if settled_fail else "")
        checkbox = (
            f'<form class="cell" method="post" action="/_inbox/click/{r["send_id"]}">'
            f'<input class="check" type="checkbox" {"checked disabled" if clicked else ""} '
            f'onchange="this.form.submit()">'
            f'</form>'
        )
        body.append(
            f'<tr class="{cls}">'
            f"<td>{checkbox}</td>"
            f'<td class="subject">{html.escape(_truncate(r["subject"], 80))}</td>'
            f'<td class="mono">{html.escape(r["recipient"])}</td>'
            f'<td><span class="cohort">{html.escape(r["cohort"])}</span></td>'
            f'<td><a href="/_inbox?campaign_id={r["campaign_id"]}">{html.escape(r["campaign_name"])}</a></td>'
            f'<td class="mono">{r["sent_at"].strftime("%H:%M:%S")}</td>'
            f"</tr>"
        )
    return head + "".join(body) + "</tbody></table>"


@router.get("/", response_class=HTMLResponse)
def inbox_view(
    request: Request,
    campaign_id: int | None = None,
    db: Session = Depends(get_db),
):
    if not _enabled():
        raise HTTPException(403, f"inbox is disabled (EIGEN_ESP={settings().esp})")

    q = (
        db.query(models.Send, models.Variant, models.Campaign)
        .join(models.Variant, models.Send.variant_id == models.Variant.id)
        .join(models.Campaign, models.Send.campaign_id == models.Campaign.id)
        .order_by(models.Send.sent_at.desc())
    )
    if campaign_id:
        q = q.filter(models.Send.campaign_id == campaign_id)
    sends = q.limit(500).all()

    recipient_ids = {s.recipient_id for s, _, _ in sends}
    recip_map = {
        r.id: r.email
        for r in db.query(models.Recipient)
        .filter(models.Recipient.id.in_(recipient_ids))
        .all()
    } if recipient_ids else {}

    clicked_send_ids = {
        e.send_id
        for e in db.query(models.Event)
        .filter(models.Event.kind == "click", models.Event.send_id.in_([s.id for s, _, _ in sends]))
        .all()
    } if sends else set()

    rows = [
        {
            "send_id": s.id,
            "subject": v.subject,
            "recipient": recip_map.get(s.recipient_id, "?"),
            "cohort": s.cohort,
            "campaign_id": c.id,
            "campaign_name": c.name,
            "sent_at": s.sent_at,
            "settled_at": s.settled_at,
            "clicked": s.id in clicked_send_ids,
        }
        for s, v, c in sends
    ]

    n_total = len(rows)
    n_clicked = sum(1 for r in rows if r["clicked"])
    n_unsettled = sum(1 for r in rows if r["settled_at"] is None)

    campaigns = db.query(models.Campaign).order_by(models.Campaign.created_at.desc()).all()
    campaign_options = "".join(
        f'<option value="{c.id}"{" selected" if campaign_id == c.id else ""}>{html.escape(c.name)}</option>'
        for c in campaigns
    )

    settle_query = f"?campaign_id={campaign_id}" if campaign_id else ""

    return PAGE.format(
        css=CSS,
        n_total=n_total,
        n_clicked=n_clicked,
        n_unsettled=n_unsettled,
        campaign_options=campaign_options,
        table=_build_table(rows, has_filter=campaign_id is not None),
        settle_query=settle_query,
        esp_mode=settings().esp,
    )


@router.post("/click/{send_id}")
def click_send(send_id: int, db: Session = Depends(get_db)):
    """Idempotent click. Records an Event row and bumps α on the right
    (variant, cohort) posterior. Replays of the same send_id are no-ops."""
    if not _enabled():
        raise HTTPException(403, "inbox disabled")

    send = db.get(models.Send, send_id)
    if not send:
        raise HTTPException(404, "send not found")

    provider_event_id = f"inbox-click-{send_id}"
    existing = (
        db.query(models.Event)
        .filter_by(provider="inbox", provider_event_id=provider_event_id)
        .first()
    )
    if existing:
        return RedirectResponse(_redirect_back(send.campaign_id), status_code=303)

    db.add(
        models.Event(
            send_id=send_id,
            kind="click",
            provider="inbox",
            provider_event_id=provider_event_id,
            raw={"source": "inbox"},
        )
    )
    if send.settled_at is None:
        v = db.get(models.Variant, send.variant_id)
        p = get_or_create_posterior(db, v, send.cohort)
        p.alpha += 1.0
        send.settled_at = utcnow()
    db.commit()
    return RedirectResponse(_redirect_back(send.campaign_id), status_code=303)


@router.post("/settle")
def settle_unclicked(
    campaign_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Mark every un-clicked, un-settled send as a failure (β += 1)."""
    if not _enabled():
        raise HTTPException(403, "inbox disabled")

    q = db.query(models.Send).filter(models.Send.settled_at.is_(None))
    if campaign_id:
        q = q.filter(models.Send.campaign_id == campaign_id)
    unsettled = q.all()
    for s in unsettled:
        v = db.get(models.Variant, s.variant_id)
        p = get_or_create_posterior(db, v, s.cohort)
        p.beta += 1.0
        s.settled_at = utcnow()
    db.commit()
    return RedirectResponse(_redirect_back(campaign_id), status_code=303)


def _redirect_back(campaign_id: int | None) -> str:
    return f"/_inbox?campaign_id={campaign_id}" if campaign_id else "/_inbox/"
