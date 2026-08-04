"""Convierte la respuesta de `get_norma_json` (BCN) en un documento limpio.

Éste es el endpoint clave del proyecto: es el único que sí entrega el texto de
una norma **tal como regía en una fecha pasada** (parámetro `idVersion`). No
está documentado públicamente; se descubrió inspeccionando las peticiones de
red de la propia web de LeyChile.

Forma verificada en vivo (2026-08-02, idNorma=206396):

    {
      "html": [ <bloque>, <bloque>, ... ],  # secciones de primer nivel, en orden
      "metadatos": { "titulo_norma": ..., "organismos": [...], ... },
      "estructura": [ ... ],                # índice de contenidos (no se usa acá)
      ...
    }

Un <bloque> es:

    {"t": "<div>...fragmento html...</div>", "i": <id interno>, "h": [<bloque>, ...]}

`t` es un fragmento HTML con el texto propio de ese nodo (p. ej. el
encabezado "Párrafo N", o el cuerpo completo de un artículo). `h`, cuando
existe, contiene los bloques hijos anidados (los Artículos de un Párrafo,
etc.), reflejando la jerarquía real de la norma:
Título > Capítulo > Párrafo > Artículo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

BLOCK_TAGS = {"div", "p", "br", "li", "tr"}
# Elementos HTML "void": no tienen etiqueta de cierre, así que nunca deben
# sumar profundidad en la pila (el contenido de BCN sólo usa <br>, pero la
# lista completa es por precaución). Contar mal la profundidad acá rompía el
# descarte de notas al pie y se comía el resto del texto del artículo.
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "wbr", "area", "base", "col", "embed", "param", "source", "track"}


class _TextExtractor(HTMLParser):
    """Reduce un fragmento HTML a texto plano, usando las etiquetas de bloque
    como saltos de párrafo.

    BCN intercala citas al pie *dentro* de las frases, con la forma
    `<span class="n" id="n_X">LEY ... Art. ... D.O. ...</span>` (a veces con un
    `<a>` anidado), marcando el punto exacto que tocó una modificación
    posterior. Son anotaciones de la interfaz de BCN, no parte del texto legal
    —y además el commit correspondiente ya lleva esa misma cita en sus
    metadatos—, así que su contenido se descarta en vez de insertarse.

    Si se insertara, corrompería la frase que lo rodea: no hay espacio del lado
    de *entrada*, por lo que "Las<span>...</span> producciones" quedaría como
    "LasLEY 20756... producciones".
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
            return  # no sumaron profundidad; el parser no debería llamar acá igual
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
    """Nodo de la norma: un agrupador (con hijos) o un artículo (hoja)."""

    text: str
    children: list["Block"]

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class NormaDocument:
    """Una norma completa en una fecha determinada, lista para renderizar."""

    id_norma: int
    version_date: str
    titulo_norma: str
    organismos: list[str]
    fecha_publicacion: str
    blocks: list[Block]
    compuesto: str = ""  # p. ej. "LEY-19846", desde metadatos.tipos_numeros


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
