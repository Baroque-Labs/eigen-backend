from pydantic import BaseModel, EmailStr, Field


class BaselineVariant(BaseModel):
    subject: str
    body: str = ""
    true_ctr: float = Field(0.05, ge=0.0, le=1.0)  # smoke-screen


class RecipientIn(BaseModel):
    email: EmailStr
    cohort: str = "default"


class Calendar(BaseModel):
    """When the scheduler may dispatch. Empty list = any."""
    weekdays: list[int] = Field(default_factory=list, description="ISO 1=Mon..7=Sun")
    hours: list[int] = Field(default_factory=list, description="0..23, in campaign tz")


class CampaignIn(BaseModel):
    name: str
    baseline: BaselineVariant
    n_variants: int = Field(4, ge=1, le=64)
    batch_size: int = Field(100, ge=1, description="Recipients per dispatched batch")
    cadence_minutes: int = Field(60, ge=1, description="Sim-minutes between batches")
    calendar: Calendar = Field(default_factory=Calendar)
    timezone: str = "UTC"
    settle_window_seconds: int = Field(86400, ge=1, description="Sim-seconds before un-clicked = failure")
    # Two ways to specify recipients: plain emails (all in "default" cohort) or
    # the richer form with explicit cohorts. Both can be combined.
    emails: list[EmailStr] = Field(default_factory=list)
    recipients: list[RecipientIn] = Field(default_factory=list)

    def all_recipients(self) -> list[RecipientIn]:
        out = [RecipientIn(email=e) for e in self.emails]
        out.extend(self.recipients)
        return out


class EventIn(BaseModel):
    send_id: int
    kind: str = "click"


class CohortPosterior(BaseModel):
    cohort: str
    alpha: float
    beta: float
    mean: float
    samples: float
    prob_best: float


class VariantOut(BaseModel):
    id: int
    subject: str
    body: str
    status: str
    parent_id: int | None
    cohorts: list[CohortPosterior]


class CampaignState(BaseModel):
    id: int
    name: str
    status: str
    n_variants: int
    batch_size: int
    cadence_minutes: int
    calendar: Calendar
    timezone: str
    settle_window_seconds: int
    variants: list[VariantOut]
    total_sends: int
    total_clicks: int
    stopped_reason: str | None = None
