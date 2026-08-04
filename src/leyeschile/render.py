"""Convierte un `NormaDocument` ya parseado en Markdown.

Cada archivo generado lleva al inicio un bloque de front-matter de auditoría
que declara exactamente qué URL de BCN lo produjo y cuándo se descargó, para
que cualquier persona pueda volver a pedir esa misma URL y verificar el
contenido de forma independiente.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .norma_json import Block, NormaDocument

MAX_HEADING_DEPTH = 6


def _render_block(block: Block, depth: int) -> list[str]:
    """Renderiza un nodo y sus hijos. Los agrupadores (los que tienen hijos)
    quedan como encabezados Markdown; los artículos, como texto plano."""
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
    """Documento completo en Markdown, con el front-matter de auditoría."""
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
