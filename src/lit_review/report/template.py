"""Markdown report template.

The template is data-driven: a list of (section_name, section_title) pairs.
Sections are rendered in order; missing bodies fall back to a placeholder
(used only when a section is absent from the final submitted drafts).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionSpec:
    name: str
    title: str
    placeholder: str


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "background",
        "Background",
        "_No background section was generated. Fill in a 1–2 paragraph overview of the topic._",
    ),
    SectionSpec(
        "methods",
        "Key Methods",
        "_No methods section was generated. Summarize 3–5 representative approaches from the references._",
    ),
    SectionSpec(
        "datasets",
        "Datasets & Benchmarks",
        "_No datasets section was generated. List 3–5 commonly used datasets/benchmarks._",
    ),
    SectionSpec(
        "trends",
        "Trends",
        "_No trends section was generated. Discuss 3–5 emerging directions._",
    ),
    SectionSpec(
        "open_problems",
        "Open Problems & Future Directions",
        "_No open-problems section was generated. List 3–5 unsolved questions._",
    ),
)




def render_report(
    *,
    topic: str,
    language: str,
    generated_on: str,
    sections: dict[str, str],
    references: list,
) -> str:
    """Build the final Markdown document."""
    title = f"# Literature Review: {topic}"
    parts: list[str] = [title, ""]
    parts.append(f"_Generated on {generated_on} · Language: {language}_")
    parts.append("")

    # Table of contents.
    parts.append("## Table of Contents")
    parts.append("")
    for spec in SECTIONS:
        parts.append(f"- [{spec.title}](#{spec.title.lower().replace(' ', '-').replace('&', '').replace('---', '-')})")
    parts.append(f"- [References](#references)")
    parts.append("")

    for spec in SECTIONS:
        parts.append(f"## {spec.title}")
        parts.append("")
        parts.append(sections.get(spec.name) or spec.placeholder)
        parts.append("")

    parts.append("## References")
    parts.append("")
    if not references:
        parts.append("_No references collected._")
    else:
        for i, p in enumerate(references, 1):
            parts.append(p.display_ref(i))
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"
