from eigen.generator import GeneratedVariant
from eigen.generator.guardrails import passes_guardrails


def _v(subject: str, body: str = "<p>hello</p>") -> GeneratedVariant:
    return GeneratedVariant(subject=subject, body=body)


def test_rejects_too_long():
    assert not passes_guardrails(_v("x" * 81), history=[], parent_subject="hi")


def test_rejects_all_caps():
    assert not passes_guardrails(_v("BUY NOW LIMITED TIME"), history=[], parent_subject="hi")


def test_rejects_banned_patterns():
    assert not passes_guardrails(_v("Re: hello"), history=[], parent_subject="hi")
    assert not passes_guardrails(_v("100% FREE deal"), history=[], parent_subject="hi")
    assert not passes_guardrails(_v("read this!!!"), history=[], parent_subject="hi")


def test_rejects_dupes():
    assert not passes_guardrails(_v("Hello there"), history=[], parent_subject="hello   there")
    assert not passes_guardrails(_v("seen this"), history=["Seen This"], parent_subject="parent")


def test_accepts_reasonable():
    assert passes_guardrails(_v("Big news inside"), history=["Hello there"], parent_subject="Old subject")
