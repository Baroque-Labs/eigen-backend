from pydantic import BaseModel, EmailStr, Field


class VariantIn(BaseModel):
    subject: str
    body: str = ""
    true_ctr: float = Field(0.05, ge=0.0, le=1.0)  # smoke-screen


class CampaignIn(BaseModel):
    name: str
    variants: list[VariantIn]


class RecipientIn(BaseModel):
    email: EmailStr


class RecipientsIn(BaseModel):
    recipients: list[RecipientIn]


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
    samples: int
    prob_best: float
    parent_id: int | None


class CampaignState(BaseModel):
    id: int
    name: str
    variants: list[VariantOut]
    total_sends: int
    total_clicks: int
