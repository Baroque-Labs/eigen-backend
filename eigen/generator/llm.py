"""LLM-backed variant generator using the Anthropic SDK.

Uses messages.parse() for structured output. Caches the system prompt — for
high-volume use the same system prompt + recent-history layout caches cleanly.
"""
import anthropic
from pydantic import BaseModel, Field

from eigen.config import settings
from eigen.generator import GeneratedVariant
from eigen.generator.guardrails import passes_guardrails

SYSTEM_PROMPT = """You write email subject lines and short HTML bodies for A/B testing.

Your job is to propose ONE new variant that is meaningfully different from the parent
and from prior variants you've already generated for this campaign. Different doesn't
mean random — keep the same core offer/CTA. Vary tone, framing, length, specificity,
or angle of appeal.

Constraints:
- subject: <= 80 chars, no all-caps, no excessive emoji (one is fine), no "RE:" or "FW:" tricks
- body: short, valid HTML (a few <p> tags), include exactly one CTA
- don't echo the parent subject; meaningfully different wording or framing
- don't echo any subject in the history list

Return JSON with `subject` and `body` fields."""


class VariantSchema(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)


class LLMGenerator:
    name = "llm"

    def __init__(self) -> None:
        api_key = settings().anthropic_api_key
        if not api_key:
            raise RuntimeError("EIGEN_ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = settings().llm_model

    def generate(self, *, parent_subject: str, parent_body: str, history: list[str]) -> GeneratedVariant:
        history_text = "\n".join(f"- {h}" for h in history[-20:]) or "(none yet)"
        user_msg = (
            f"Parent variant:\n  subject: {parent_subject!r}\n  body: {parent_body!r}\n\n"
            f"Prior variants in this campaign (avoid these):\n{history_text}\n\n"
            "Propose one new variant."
        )

        for attempt in range(3):
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=2000,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
                output_format=VariantSchema,
            )
            parsed: VariantSchema = response.parsed_output
            candidate = GeneratedVariant(subject=parsed.subject.strip(), body=parsed.body.strip())
            if passes_guardrails(candidate, history=history, parent_subject=parent_subject):
                return candidate
            # else: retry with the bad candidate added to history so the model avoids it
            history = history + [candidate.subject]

        # If still no luck, return last candidate anyway — caller decides whether to keep.
        return candidate
