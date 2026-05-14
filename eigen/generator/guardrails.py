"""Post-generation filters."""
import re

from eigen.generator import GeneratedVariant

MAX_SUBJECT_LEN = 80
BANNED_PATTERNS = [
    re.compile(r"^(re|fw|fwd)\s*:", re.IGNORECASE),
    re.compile(r"100%\s*free", re.IGNORECASE),
    re.compile(r"!!!+"),
]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def passes_guardrails(v: GeneratedVariant, *, history: list[str], parent_subject: str) -> bool:
    if len(v.subject) > MAX_SUBJECT_LEN:
        return False
    if v.subject.isupper():
        return False
    for pat in BANNED_PATTERNS:
        if pat.search(v.subject):
            return False
    norm = _normalize(v.subject)
    if norm == _normalize(parent_subject):
        return False
    if any(norm == _normalize(h) for h in history):
        return False
    return True
