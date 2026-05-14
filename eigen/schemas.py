from pydantic import BaseModel, EmailStr, Field


class BaselineVariant(BaseModel):
    subject: str
    body: str = ""
    true_ctr: float = Field(0.05, ge=0.0, le=1.0)  # smoke-screen


class RecipientIn(BaseModel):
    email: EmailStr
    cohort: str = "default"


class CampaignIn(BaseModel):
    name: str
    baseline: BaselineVariant
    n_variants: int = Field(4, ge=1, le=64)
    n_batches: int = Field(10, ge=1)
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
    status: str
    parent_id: int | None
    cohorts: list[CohortPosterior]


class CampaignState(BaseModel):
    id: int
    name: str
    status: str
    n_variants: int
    n_batches: int
    batch_size: int
    variants: list[VariantOut]
    total_sends: int
    total_clicks: int
    stopped_reason: str | None = None
