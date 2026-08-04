"""Parse BCN's `get_norma_json` response into a clean, renderable document.

Confirmed live (2026-08-02) shape (idNorma=206396):

    {
      "html": [ <block>, <block>, ... ],   # top-level sections, in order
      "metadatos": { "titulo_norma": ..., "organismos": [...], ... },
      "estructura": [ ... ],               # table of contents (unused here)
      ...
    }

A <block> is:

    {"t": "<div>...html fragment...</div>", "i": <internal id>, "h": [<block>, ...]}

`t` is an HTML fragment for that node's own text (e.g. a "Párrafo N" header,
or an article's full body); `h`, when present, holds nested child blocks
(a Párrafo's Artículos, etc.) — this mirrors the norm's real structural
hierarchy (Título > Capítulo > Párrafo > Artículo).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

BLOCK_TAGS = {"div", "p", "br", "li", "tr"}
# HTML void elements: no closing tag, so they must never push onto the
# depth stack (BCN content only uses <br>, but this is defensive).
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "wbr", "area", "base", "col", "embed", "param", "source", "track"}


class _TextExtractor(HTMLParser):
    """Strip an HTML fragment down to plain text, keeping block-level tags
    as paragraph breaks.

    BCN inlines footnote-style citations mid-sentence as
    `<span class="n" id="n_X">LEY ... Art. ... D.O. ...</span>`, wrapping
    the exact point a later amendment touched the text (sometimes with a
    nested `<a>`). These are BCN UI annotations, not part of the legal
    text itself (and its own commit already carries the same citation via
    version metadata), so their content is dropped rather than spliced in
    -- otherwise it corrupts the surrounding sentence (no space is present
    on the *entering* side, e.g. "Las<span>...</span> producciones" would
    render as "LasLEY 20756... producciones" if inlined).
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._depth = 0
        self._suppress_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        is_void = tag in VOID_TAGS
        if not is_void:
            self._depth += 1
        if self._suppress_depth is None:
            classes = (dict(attrs).get("class") or "").split()
            if tag == "span" and "n" in classes:
                self._suppress_depth = self._depth
            elif tag in BLOCK_TAGS:
                self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS and self._suppress_depth is None:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return  # no depth was pushed for these; parsers shouldn't call this anyway
        if self._suppress_depth is not None:
            if self._depth == self._suppress_depth:
                self._suppress_depth = None
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._suppress_depth is None:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = raw.replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)


def html_fragment_to_text(fragment: str) -> str:
    parser = _TextExtractor()
    parser.feed(fragment)
    return parser.text()


@dataclass
class Block:
    text: str
    children: list["Block"]

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class NormaDocument:
    id_norma: int
    version_date: str
    titulo_norma: str
    organismos: list[str]
    fecha_publicacion: str
    blocks: list[Block]
    compuesto: str = ""  # e.g. "LEY-19846", from metadatos.tipos_numeros


def _parse_block(raw: dict) -> Block:
    text = html_fragment_to_text(raw.get("t", ""))
    children = [_parse_block(child) for child in raw.get("h", [])]
    return Block(text=text, children=children)


def parse_norma_json(raw_json: bytes | str, *, id_norma: int, version_date: str) -> NormaDocument:
    data = json.loads(raw_json)
    metadatos = data.get("metadatos", {})
    blocks = [_parse_block(b) for b in data.get("html", [])]
    tipos_numeros = metadatos.get("tipos_numeros") or [{}]
    return NormaDocument(
        id_norma=id_norma,
        version_date=version_date,
        titulo_norma=metadatos.get("titulo_norma", ""),
        organismos=metadatos.get("organismos", []),
        fecha_publicacion=metadatos.get("fecha_publicacion", ""),
        blocks=blocks,
        compuesto=tipos_numeros[0].get("compuesto", ""),
    )
