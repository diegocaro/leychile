"""Catálogo oficial de tipos de norma de BCN.

BCN publica su propia lista de tipos en `getTiposNorma` (39 entradas,
verificado en vivo el 2026-08-04). Usarla evita inventar la traducción de las
siglas que aparecen en las modificatorias: `AA` es "Auto Acordado", `SEN` es
"Sentencia", `REC` es "Rectificación", etc.

Cada entrada trae:

    {"abbr": "AA", "valor": "Auto Acordado", "cod": 4, "otro": "0", "orden": 4}

El campo `otro` distingue las normas que la propia BCN considera principales
(`"1"`: Ley, Decreto, DFL, Decreto Ley, Resolución...) de los instrumentos
secundarios (`"0"`: Auto Acordado, Sentencia, Rectificación, Aviso,
Dictamen...). Es útil como señal de jerarquía, aunque no reemplaza al análisis
jurídico.

Dato curioso del catálogo: existen los tipos `COD` ("Código") y `CTR`
("Constitución de la República"), pero los documentos que seguimos no son de
esos tipos, sino los decretos que fijan su texto (el Código Civil es un DFL, la
Constitución vigente un DTO). Por eso su historia empieza en la fecha del
decreto refundido y no en la del cuerpo legal original: es justamente lo que
resuelve el linaje (ver `discover.CONSTITUCION_LINAJE`).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from .client import BcnClient

GET_TIPOS_NORMA_URL = "https://nuevo.leychile.cl/servicios/Consulta/getTiposNorma"


@dataclass(frozen=True)
class TipoNorma:
    """Un tipo de norma según el catálogo de BCN."""

    abbr: str
    nombre: str
    principal: bool  # el campo `otro` de BCN: True para leyes/decretos, False para autos acordados, sentencias...

    @property
    def slug(self) -> str:
        """Nombre de carpeta: "Decreto con Fuerza de Ley" -> "decreto-con-fuerza-de-ley"."""
        normalizado = unicodedata.normalize("NFKD", self.nombre.lower())
        ascii_txt = "".join(c for c in normalizado if (c.isalnum() and ord(c) < 128) or c in " -")
        return re.sub(r"-+", "-", re.sub(r"\s+", "-", ascii_txt.strip())) or self.abbr.lower()


def fetch_catalogo(client: BcnClient) -> dict[str, TipoNorma]:
    """Catálogo completo, indexado por sigla (`abbr`)."""
    data = json.loads(client.get(GET_TIPOS_NORMA_URL).text())
    catalogo: dict[str, TipoNorma] = {}
    for entrada in data:
        abbr = (entrada.get("abbr") or "").strip()
        if not abbr:
            continue
        # El catálogo trae "LEI" dos veces ("Norma Antigua" y "Lei"); nos
        # quedamos con la primera, que es la descripción más útil.
        catalogo.setdefault(
            abbr,
            TipoNorma(
                abbr=abbr,
                nombre=(entrada.get("valor") or abbr).strip(),
                principal=str(entrada.get("otro", "0")) == "1",
            ),
        )
    return catalogo


def describir(catalogo: dict[str, TipoNorma], abbr: str) -> TipoNorma:
    """Tipo de norma, con un respaldo razonable si BCN devuelve una sigla que
    no está en su propio catálogo."""
    abbr = (abbr or "").strip()
    if abbr in catalogo:
        return catalogo[abbr]
    return TipoNorma(abbr=abbr or "OTR", nombre=abbr or "Norma", principal=False)


# Verbo con el que se describe en el mensaje del commit lo que hizo cada tipo
# de norma. No son sinónimos: una sentencia del Tribunal Constitucional no
# "modifica" un código, lo deroga en la parte declarada inconstitucional; y una
# rectificación del Diario Oficial corrige una errata, no reforma nada.
VERBOS_POR_TIPO = {
    "SEN": "deroga parte de",
    "REC": "rectifica el texto de",
    "AVI": "complementa",
}
VERBO_POR_DEFECTO = "modifica"


def verbo_para(abbr: str) -> str:
    return VERBOS_POR_TIPO.get((abbr or "").strip(), VERBO_POR_DEFECTO)
