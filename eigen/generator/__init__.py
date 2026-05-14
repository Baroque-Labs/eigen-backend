"""Variant generators. Same interface; swap implementations via EIGEN_GENERATOR."""
from dataclasses import dataclass
from typing import Protocol

from eigen.config import settings


@dataclass
class GeneratedVariant:
    subject: str
    body: str


class Generator(Protocol):
    name: str

    def generate(self, *, parent_subject: str, parent_body: str, history: list[str]) -> GeneratedVariant: ...


def get_generator() -> Generator:
    name = settings().generator
    if name == "template":
        from eigen.generator.template import TemplateGenerator

        return TemplateGenerator()
    if name == "llm":
        from eigen.generator.llm import LLMGenerator

        return LLMGenerator()
    raise ValueError(f"unknown generator: {name!r}")
