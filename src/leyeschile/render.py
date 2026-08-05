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


def _una_linea(texto: str) -> str:
    """Colapsa saltos de línea y comillas para que un valor sea YAML válido.

    Los títulos de BCN vienen con saltos de línea incrustados (p. ej. "SENTENCIA
    DICTADA POR EL TRIBUNAL CONSTITUCIONAL QUE\\nDECLARA INCONSTITUCIONAL..."),
    que romperían el front-matter si se escribieran tal cual.
    """
    return " ".join((texto or "").split()).replace('"', "'")


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


def render_markdown_norma(
    doc: NormaDocument,
    *,
    source_url: str,
    fetched_at: str,
    tipo_abbr: str,
    tipo_nombre: str,
    numero: str,
    organismo: str,
    fecha_vigencia: str,
    fecha_publicacion: str = "",
) -> str:
    """Markdown de una norma modificatoria (una ley, un decreto ley, un auto
    acordado...), con su propio front-matter de auditoría.

    Se guarda el texto tal como regía al entrar en vigencia, o sea prácticamente
    el original: es lo coherente con un repositorio de historia ("esto fue lo
    que se promulgó") y además es lo que ya quedó en caché al buscar los
    firmantes de cada modificación.
    """
    front_matter = [
        "---",
        f"source_url: {source_url}",
        f"id_norma: {doc.id_norma}",
        f"tipo: {tipo_abbr}",
        f'tipo_nombre: "{_una_linea(tipo_nombre)}"',
        f'numero: "{_una_linea(numero)}"',
        f'titulo: "{_una_linea(doc.titulo_norma)}"',
        f'organismo: "{_una_linea(organismo)}"',
        # Dos fechas distintas a propósito: cuándo se publicó esta norma, y
        # desde cuándo surtió efecto sobre el documento que modificó (pueden
        # diferir, incluso con efecto retroactivo).
        f"fecha_publicacion: {fecha_publicacion or doc.fecha_publicacion}",
        f"fecha_vigencia_de_la_modificacion: {fecha_vigencia}",
        # Estado actual de la norma completa. Sin esto, el archivo de una ley
        # derogada se lee como si siguiera vigente.
        f"derogada: {'true' if doc.derogado else 'false'}",
        *([f"fecha_derogacion: {doc.fecha_derogacion}"] if doc.fecha_derogacion else []),
        f"fetched_at: {fetched_at}",
        "---",
        "",
        f"# {_una_linea(tipo_nombre)} {_una_linea(numero)}",
        "",
        f"*{_una_linea(doc.titulo_norma)}*",
        "",
    ]
    if doc.derogado:
        cuando = f" el {doc.fecha_derogacion}" if doc.fecha_derogacion else ""
        front_matter += [
            f"> **NORMA DEROGADA{cuando}.** El texto que sigue es el que tuvo mientras "
            "estuvo vigente; se conserva por su valor histórico, pero ya no rige.",
            "",
        ]
    body: list[str] = []
    for block in doc.blocks:
        body.extend(_render_block(block, depth=1))
    return "\n".join(front_matter + body).rstrip() + "\n"


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
        f'titulo_norma: "{_una_linea(doc.titulo_norma)}"',
        f"compuesto: {doc.compuesto}",
        f"organismos: {[_una_linea(o) for o in doc.organismos]!r}",
        f"fecha_publicacion_original: {doc.fecha_publicacion}",
        # Hoy los 16 documentos que seguimos están vigentes, pero el dato se
        # registra igual: si alguno se deroga, el archivo lo dirá solo.
        f"derogada: {'true' if doc.derogado else 'false'}",
        *([f"fecha_derogacion: {doc.fecha_derogacion}"] if doc.fecha_derogacion else []),
        "---",
        "",
        f"# {_una_linea(doc.titulo_norma)}",
        "",
    ]
    if doc.derogado:
        cuando = f" el {doc.fecha_derogacion}" if doc.fecha_derogacion else ""
        front_matter += [
            f"> **NORMA DEROGADA{cuando}.** El texto que sigue es el que tuvo mientras "
            "estuvo vigente; se conserva por su valor histórico, pero ya no rige.",
            "",
        ]

    body: list[str] = []
    for block in doc.blocks:
        body.extend(_render_block(block, depth=1))

    return "\n".join(front_matter + body).rstrip() + "\n"
