"""Extract real signer names from a norm's Promulgación text.

Every norm's rendered text (already fetched for content) includes a
Promulgación block naming who actually signed it into law — e.g. Código
Aeronáutico's ends with:

    JOSE T. MERINO CASTRO, Almirante, ... Miembro de la Junta de Gobierno.-
    ...
    Santiago, 19 de enero de 1990.- AUGUSTO PINOCHET UGARTE, Capitán
    General, Presidente de la República.- Hugo Rosende Subiabre, Ministro
    de Justicia.
    Lo que transcribo a Ud. para su conocimiento.- ... Subsecretario ...

This is real, almost-universally-present data that `get_autores_de_la_ley`
doesn't cover (that endpoint only has named authors for laws that began as
a parliamentary "moción"). Best-effort text parsing: BCN separates
signatures with ".-", but capitalization is inconsistent (President/Junta
signatures are often all-caps, Ministers often title-case), so matching is
permissive on name casing and keys off role keywords instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .norma_json import Block, NormaDocument

_NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü.]*"
SIGNER_SEGMENT_RE = re.compile(rf"^\s*((?:{_NAME_WORD}\s+){{1,4}}{_NAME_WORD})\s*,\s*(.+?)\s*$")

PROMULGACION_MARKER_RE = re.compile(
    r"ll[eé]vese a efecto|prom[uú]lguese|reg[ií]strese en la contralor[ií]a|"
    r"publ[ií]quese en el diario oficial|t[oó]mese raz[oó]n",
    re.IGNORECASE,
)
PRESIDENT_ROLE_RE = re.compile(r"presidente de la rep[uú]blica", re.IGNORECASE)
JUNTA_ROLE_RE = re.compile(r"junta de gobierno", re.IGNORECASE)
MINISTER_ROLE_RE = re.compile(r"\bministr[oa]\b", re.IGNORECASE)
SUBSECRETARIO_ROLE_RE = re.compile(r"subsecretari[oa]", re.IGNORECASE)
TRANSCRIBER_MARKER_RE = re.compile(r"lo que transcribo", re.IGNORECASE)
ROLE_MAX_LEN = 100


@dataclass(frozen=True)
class Signer:
    name: str
    role: str


def _find_promulgacion_block(blocks: list[Block]) -> Block | None:
    # Promulgación is always near the end (right before any Anexos), and the
    # strong markers below only otherwise risk matching Encabezado
    # boilerplate for junta-era laws — searching from the end avoids that.
    for block in reversed(blocks):
        if block.text and PROMULGACION_MARKER_RE.search(block.text):
            return block
    return None


def extract_signers(doc: NormaDocument) -> list[Signer]:
    block = _find_promulgacion_block(doc.blocks)
    if block is None:
        return []
    text = block.text
    cut = TRANSCRIBER_MARKER_RE.search(text)
    if cut:
        text = text[: cut.start()]

    signers: list[Signer] = []
    for segment in text.split(".-"):
        segment = " ".join(segment.split())  # collapse newlines/whitespace
        match = SIGNER_SEGMENT_RE.match(segment)
        if not match:
            continue
        name, role = match.group(1).strip(), match.group(2).strip().rstrip(".")
        if len(role) > ROLE_MAX_LEN:
            # A missing ".-" delimiter before trailing boilerplate prose can
            # pull an entire extra sentence into the role; cut at the first
            # sentence boundary as a sane fallback.
            cut_at = role.find(". ")
            role = role[:cut_at] if 0 < cut_at <= ROLE_MAX_LEN else role[:ROLE_MAX_LEN]
        signers.append(Signer(name=name, role=role))
    return signers


def primary_signer(signers: list[Signer]) -> Signer | None:
    for s in signers:
        if PRESIDENT_ROLE_RE.search(s.role):
            return s
    for s in signers:
        if JUNTA_ROLE_RE.search(s.role):
            return s
    return signers[0] if signers else None


def minister_signer(signers: list[Signer], *, exclude: Signer | None = None) -> Signer | None:
    for s in signers:
        if s is exclude:
            continue
        if MINISTER_ROLE_RE.search(s.role) and not SUBSECRETARIO_ROLE_RE.search(s.role):
            return s
    return None
