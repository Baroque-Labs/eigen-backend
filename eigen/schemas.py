from pydantic import BaseModel, EmailStr, Field


class BaselineVariant(BaseModel):
    subject: str
    body: str = ""
    true_ctr: float = Field(0.05, ge=0.0, le=1.0)  # smoke-screen


class CampaignIn(BaseModel):
    name: str
    baseline: BaselineVariant
    n_variants: int = Field(4, ge=1, le=64)
    n_batches: int = Field(10, ge=1)
    emails: list[EmailStr]


class EventIn(BaseModel):
    send_id: int
    kind: str = "click"


class VariantOut(BaseModel):
    id: int
    subject: str
    status: str
    alpha: float
    beta: float
    mean: float
    samples: float
    prob_best: float
    parent_id: int | None


class CampaignState(BaseModel):
    id: int
    name: str
    n_variants: int
    n_batches: int
    batch_size: int
    variants: list[VariantOut]
    total_sends: int
    total_clicks: int
