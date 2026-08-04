"""Render a parsed NormaDocument to Markdown, with an audit front-matter
block so every file states exactly which BCN URL produced it and when."""

from __future__ import annotations

from datetime import datetime, timezone

from .norma_json import Block, NormaDocument

MAX_HEADING_DEPTH = 6


def _render_block(block: Block, depth: int) -> list[str]:
    lines: list[str] = []
    if block.text:
        if block.children:
            heading_level = min(depth + 1, MAX_HEADING_DEPTH)
            lines.append(f"{'#' * heading_level} {block.text}")
        else:
            lines.append(block.text)
        lines.append("")
    for child in block.children:
        lines.extend(_render_block(child, depth + 1))
    return lines


def render_markdown(
    doc: NormaDocument,
    *,
    source_url: str,
    fetched_at: str | None = None,
) -> str:
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    front_matter = [
        "---",
        f"source_url: {source_url}",
        f"id_norma: {doc.id_norma}",
        f"version_date: {doc.version_date}",
        f"fetched_at: {fetched_at}",
        f'titulo_norma: "{doc.titulo_norma}"',
        f"compuesto: {doc.compuesto}",
        f"organismos: {doc.organismos!r}",
        f"fecha_publicacion_original: {doc.fecha_publicacion}",
        "---",
        "",
        f"# {doc.titulo_norma}",
        "",
    ]

    body: list[str] = []
    for block in doc.blocks:
        body.extend(_render_block(block, depth=1))

    return "\n".join(front_matter + body).rstrip() + "\n"
