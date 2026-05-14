from eigen.generator import GeneratedVariant


class TemplateGenerator:
    name = "template"

    def generate(self, *, parent_subject: str, parent_body: str, history: list[str]) -> GeneratedVariant:
        return GeneratedVariant(subject=f"{parent_subject} (variant)", body=parent_body)
