"""Extrae los nombres de quienes firmaron una norma, desde su Promulgación.

El texto de toda norma (que igual descargamos por su contenido) incluye un
bloque de Promulgación con las firmas reales. Por ejemplo, el Código
Aeronáutico termina así:

    JOSE T. MERINO CASTRO, Almirante, ... Miembro de la Junta de Gobierno.-
    ...
    Santiago, 19 de enero de 1990.- AUGUSTO PINOCHET UGARTE, Capitán
    General, Presidente de la República.- Hugo Rosende Subiabre, Ministro
    de Justicia.
    Lo que transcribo a Ud. para su conocimiento.- ... Subsecretario ...

Este dato es real y está presente en casi todas las normas, a diferencia de
`get_autores_de_la_ley`, que sólo trae autores con nombre para las leyes que
nacieron como moción parlamentaria.

El parseo es "lo mejor posible": BCN separa las firmas con ".-", pero la
capitalización es inconsistente (las firmas del Presidente y de la Junta suelen
ir en mayúsculas, las de los ministros en formato título). Por eso el criterio
no se basa en cómo está escrito el nombre, sino en las palabras clave del cargo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .norma_json import Block, NormaDocument

# El guion es parte del nombre: sin él, los apellidos compuestos no matchean y
# la firma se descarta entera y en silencio. Así se perdía a EDUARDO FREI
# RUIZ-TAGLE en todas sus leyes, y a ministros como Andrés Gómez-Lobo.
_NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü.\-]*"
# Partículas de apellido en minúscula: sin esto, el mismo problema del guion se
# repite con cualquier firmante cuyo apellido lleve una de estas palabras. Se
# perdía por completo, p. ej., a Esteban Valenzuela VAN TREEK (Ministro de
# Agricultura, en al menos 6 leyes) y a Ramón DE LA Cavareda (Ministro de
# Guerra i Marina, en la promulgación original de la Constitución de 1833).
_NAME_PARTICLE = r"(?:de|del|la|las|los|van|von|der|den|dos|das|du)"
_NAME_ANY_WORD = rf"(?:{_NAME_WORD}|{_NAME_PARTICLE})"
SIGNER_SEGMENT_RE = re.compile(rf"^\s*({_NAME_WORD}(?:\s+{_NAME_ANY_WORD}){{1,6}})\s*,\s*(.+?)\s*$")

PROMULGACION_MARKER_RE = re.compile(
    r"ll[eé]vese a efecto|prom[uú]lguese|reg[ií]strese en la contralor[ií]a|"
    r"publ[ií]quese en el diario oficial|t[oó]mese raz[oó]n",
    re.IGNORECASE,
)
# "Presidenta" además de "Presidente": si sólo se busca el masculino, los
# períodos de Michelle Bachelet no matchean y la firma presidencial se pierde.
PRESIDENT_ROLE_RE = re.compile(r"president[ae] de la rep[uú]blica", re.IGNORECASE)
JUNTA_ROLE_RE = re.compile(r"junta de gobierno", re.IGNORECASE)
MINISTER_ROLE_RE = re.compile(r"\bministr[oa]\b", re.IGNORECASE)
SUBSECRETARIO_ROLE_RE = re.compile(r"subsecretari[oa]", re.IGNORECASE)
TRANSCRIBER_MARKER_RE = re.compile(r"lo que transcrib[oe]", re.IGNORECASE)
ROLE_MAX_LEN = 100

# Respaldo para detectar el bloque de promulgación cuando no trae ninguna de las
# frases rituales de arriba. Pasa de verdad: en la Ley 20.773 el bloque final
# empieza directamente con "Santiago, 15 de septiembre de 2014.- MICHELLE
# BACHELET JERIA, Presidenta de la República.- ...", sin "promúlguese" ni
# "llévese a efecto". Buscar cargos de firma recupera esos casos.
FIRMA_ROLE_RE = re.compile(
    r"president[ae] de la rep[uú]blica|junta de gobierno|ministr[oa]\s+d", re.IGNORECASE
)


@dataclass(frozen=True)
class Signer:
    """Una firma de la promulgación: nombre y cargo."""

    name: str
    role: str


def _find_promulgacion_block(blocks: list[Block]) -> Block | None:
    # La Promulgación siempre está cerca del final (justo antes de los Anexos).
    # Buscamos desde el final porque, en las normas de la época de la Junta, el
    # Encabezado contiene frases parecidas que pueden confundirse con firmas.
    for block in reversed(blocks):
        if block.text and PROMULGACION_MARKER_RE.search(block.text):
            return block
    # Segunda pasada: bloques que no traen las frases rituales pero sí cargos de
    # firma (ver FIRMA_ROLE_RE). Va después para que las frases rituales tengan
    # prioridad cuando existen.
    for block in reversed(blocks):
        if block.text and FIRMA_ROLE_RE.search(block.text):
            return block
    return None


def extract_signers(doc: NormaDocument) -> list[Signer]:
    """Devuelve todas las firmas encontradas en la promulgación de la norma."""
    block = _find_promulgacion_block(doc.blocks)
    if block is None:
        return []
    text = block.text
    cut = TRANSCRIBER_MARKER_RE.search(text)
    if cut:
        text = text[: cut.start()]

    signers: list[Signer] = []
    anchor_index: int | None = None
    for segment in text.split(".-"):
        segment = " ".join(segment.split())  # colapsa saltos de línea y espacios
        match = SIGNER_SEGMENT_RE.match(segment)
        if not match:
            continue
        name, role = match.group(1).strip(), match.group(2).strip().rstrip(".")
        if len(role) > ROLE_MAX_LEN:
            # Si falta el separador ".-" antes del texto de cierre, el cargo se
            # puede llevar una frase entera de más. Cortamos en el primer punto
            # como salida razonable.
            cut_at = role.find(". ")
            role = role[:cut_at] if 0 < cut_at <= ROLE_MAX_LEN else role[:ROLE_MAX_LEN]
        signers.append(Signer(name=name, role=role))
        # Primera firma con investidura de jefe de Estado real (Presidente o
        # Junta de Gobierno): marca dónde empieza la promulgación de verdad.
        if anchor_index is None and (PRESIDENT_ROLE_RE.search(role) or JUNTA_ROLE_RE.search(role)):
            anchor_index = len(signers) - 1

    if anchor_index is None:
        return signers

    # En normas antiguas, el mismo bloque de texto a veces junta dos actos
    # distintos: la ratificación de una sesión legislativa previa (firmada por
    # su propio "Presidente" y "Secretario" de mesa, cargos que calzan con el
    # mismo patrón "Nombre, Cargo" pero no son investidura real) y, después, la
    # promulgación ejecutiva propiamente tal. Verificado en vivo en la
    # Constitución de 1833 (idNorma=137535): el bloque trae primero la firma de
    # "Santiago Echeverz, Presidente" y "Juan Francisco Meneses, Secretario" —
    # de la sesión del Congreso Constituyente del 22 de mayo—, y sólo después
    # la promulgación real de Joaquín Prieto del 25 de mayo. Todo lo anterior a
    # la primera firma de Presidente de la República / Junta de Gobierno no es
    # parte de la promulgación y se descarta.
    return signers[anchor_index:]


def primary_signer(signers: list[Signer]) -> Signer | None:
    """La firma principal: Presidente de la República, o en su defecto un
    miembro de la Junta de Gobierno (normas de 1973-1990)."""
    for s in signers:
        if PRESIDENT_ROLE_RE.search(s.role):
            return s
    for s in signers:
        if JUNTA_ROLE_RE.search(s.role):
            return s
    return signers[0] if signers else None


def minister_signer(signers: list[Signer], *, exclude: Signer | None = None) -> Signer | None:
    """El ministro o ministra que firmó, excluyendo subsecretarios (que suelen
    aparecer sólo en la frase final de transcripción)."""
    for s in signers:
        if s is exclude:
            continue
        if MINISTER_ROLE_RE.search(s.role) and not SUBSECRETARIO_ROLE_RE.search(s.role):
            return s
    return None
