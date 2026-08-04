"""Línea de tiempo de modificaciones de una norma, vía el servicio
`get_versiones` de BCN.

Este endpoint no está documentado públicamente, pero sí es público: es el que
llama la propia aplicación web de LeyChile (se descubrió abriendo leychile.cl
en un navegador real e inspeccionando sus peticiones de red).

Verificado en vivo (2026-08-02) contra idNorma=206396 (Ley 19.846). Forma:

    {
      "Versiones": {
        "@id_norma": 206396,
        "Version": [
          {
            "@tipoVersion": "Última Versión" | "Intermedio" | "Texto Original",
            "@vigenteDesde": "2022-12-30",
            "@vigenteHasta": "2022-12-29",   # ausente en la entrada más nueva
            "UrlVersion": {"$": "https://www.leychile.cl/N?i=206396&f=2022-12-30"},
            "Modificatorias": {              # ausente en la entrada más nueva
              "Modificatoria": { ... } | [ { ... }, { ... } ]
            }
          },
          ...
        ]
      }
    }

Cada entrada de `Version` es una **ventana de vigencia**. El bloque
`Modificatorias` de una ventana describe la(s) norma(s) que la **terminaron**
(es decir, aquellas cuyo `inicioVigencia` coincide con el `vigenteDesde` de la
ventana *siguiente*). Por eso, para construir el commit de la ventana N hay que
mirar las `Modificatorias` de la ventana N-1. La ventana más antigua ("Texto
Original") no tiene predecesora: es la publicación original de la norma, sin
norma modificatoria.

Advertencia importante: `Modificatorias` también falta a veces en ventanas que
NO son la original, simplemente porque BCN no tiene registrada la norma que
causó ese cambio. Ver `VersionEvent.is_original` / `is_unknown_cause`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .client import BcnClient

GET_VERSIONES_URL_TEMPLATE = (
    "https://nuevo.leychile.cl/servicios/Consulta/get_versiones"
    "?idNorma={id_norma}&formato=json&idParte=&idsParte="
)

# Valor centinela literal que usa BCN para "vigencia diferida por evento": una
# modificación cuya entrada en vigor depende de un reglamento o evento futuro
# que todavía no ocurre, así que BCN no tiene una fecha real que entregar y
# pone este marcador (verificado en vivo, p. ej. Código de Aguas, idNorma=5605:
# tipoVersion="Con Vigencia Diferida por Evento", vigenteDesde="2222-02-02",
# sin Modificatorias).
#
# Es distinto de un cambio futuro *realmente programado*
# (tipoVersion="Con Vigencia Diferida por Fecha" con fecha real, p. ej.
# "2027-02-25"), que sí es real y se conserva en la línea de tiempo.
#
# Además, git rechaza esta fecha de plano ("fatal: invalid date format"), así
# que no podría convertirse en un commit aunque tuviera sentido intentarlo.
BCN_DEFERRED_EVENT_SENTINEL = date(2222, 2, 2)


@dataclass(frozen=True)
class Modificatoria:
    """Norma que modificó a otra, tal como la registra BCN."""

    id_norma: int
    nro_norma: str  # normalmente dígitos, pero puede ser "S/N" en decretos antiguos sin número
    tipo_norma: str
    titulo: str
    organismo: str
    fecha_publicacion: date
    inicio_vigencia: date


@dataclass(frozen=True)
class VersionEvent:
    """Un commit: el texto de la norma tal como regía desde `vigente_desde`.

    `modificatorias` contiene la(s) norma(s) que produjeron esta versión. Puede
    venir vacío por dos razones muy distintas:

    - `is_original`: es la publicación original, no la modificó nadie.
    - `is_unknown_cause`: BCN no tiene registrada la norma causante. Estos
      huecos aparecen repartidos por toda la historia de cada código
      (verificado en vivo, p. ej. Código de Comercio: las entradas de
      1865-11-24, 2001-09-27, 2005-11-24 y 2021-04-13 no traen bloque
      Modificatorias pese a no ser la original).

    Por eso sólo la marca `@tipoVersion == "Texto Original"` de BCN identifica
    de forma confiable la publicación original: deducirla de "no tiene
    modificatorias" produce falsos positivos en cada uno de esos huecos.
    """

    vigente_desde: date
    vigente_hasta: date | None
    url_version: str
    modificatorias: tuple[Modificatoria, ...]
    tipo_version: str

    @property
    def is_original(self) -> bool:
        return self.tipo_version == "Texto Original"

    @property
    def is_unknown_cause(self) -> bool:
        return not self.modificatorias and not self.is_original


_SPANISH_MONTHS = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


def _parse_bcn_date(value: str) -> date:
    """Acepta los dos formatos de fecha que mezcla `get_versiones`.

    ISO (`YYYY-MM-DD`) en @vigenteDesde/@vigenteHasta/@inicioVigencia, pero
    `DD-MMM-YYYY` con mes abreviado en español (p. ej. "30-DIC-2022") en
    @fechaPublicacion.
    """
    if "-" in value and value[:4].isdigit():
        return date.fromisoformat(value)
    day_str, month_str, year_str = value.split("-")
    return date(int(year_str), _SPANISH_MONTHS[month_str.upper()], int(day_str))


def _parse_modificatoria(raw: dict) -> Modificatoria:
    return Modificatoria(
        id_norma=int(raw["@idNorma"]),
        nro_norma=raw.get("@nroNorma") or "S/N",
        tipo_norma=raw.get("@tipoNorma", ""),
        titulo=raw.get("@titulo", ""),
        organismo=raw.get("@organismo", ""),
        fecha_publicacion=_parse_bcn_date(raw["@fechaPublicacion"]),
        inicio_vigencia=_parse_bcn_date(raw["@inicioVigencia"]),
    )


def _extract_modificatorias(version_raw: dict) -> tuple[Modificatoria, ...]:
    """`Modificatoria` viene como dict si es una sola, y como lista si son varias."""
    block = version_raw.get("Modificatorias")
    if not block:
        return ()
    raw_mods = block["Modificatoria"]
    if isinstance(raw_mods, list):
        return tuple(_parse_modificatoria(m) for m in raw_mods)
    return (_parse_modificatoria(raw_mods),)


def fetch_version_timeline(client: BcnClient, id_norma: int) -> list[VersionEvent]:
    """Devuelve todas las ventanas de vigencia de `id_norma`, de la más antigua
    a la más nueva, cada una con la(s) norma(s) que la produjeron (vacío en la
    publicación original)."""
    url = GET_VERSIONES_URL_TEMPLATE.format(id_norma=id_norma)
    result = client.get(url)
    data = json.loads(result.text())
    raw_versions = data["Versiones"]["Version"]
    if isinstance(raw_versions, dict):
        raw_versions = [raw_versions]

    # BCN las entrega de la más nueva a la más antigua. Como el bloque
    # Modificatorias de cada ventana describe lo que la TERMINÓ (o sea, lo que
    # produjo la ventana siguiente), invertimos el orden y desplazamos en uno,
    # para que cada VersionEvent cargue el cambio que efectivamente lo produjo.
    raw_versions_oldest_first = list(reversed(raw_versions))
    events: list[VersionEvent] = []
    for i, raw in enumerate(raw_versions_oldest_first):
        vigente_desde = _parse_bcn_date(raw["@vigenteDesde"])
        vigente_hasta_raw = raw.get("@vigenteHasta")
        vigente_hasta = _parse_bcn_date(vigente_hasta_raw) if vigente_hasta_raw else None
        if i == 0:
            modificatorias: tuple[Modificatoria, ...] = ()
        else:
            modificatorias = _extract_modificatorias(raw_versions_oldest_first[i - 1])
        if vigente_desde == BCN_DEFERRED_EVENT_SENTINEL:
            continue  # no es una fecha real; ver BCN_DEFERRED_EVENT_SENTINEL
        events.append(
            VersionEvent(
                vigente_desde=vigente_desde,
                vigente_hasta=vigente_hasta,
                url_version=raw["UrlVersion"]["$"],
                modificatorias=modificatorias,
                tipo_version=raw.get("@tipoVersion", ""),
            )
        )
    return events
