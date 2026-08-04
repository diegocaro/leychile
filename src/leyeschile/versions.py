"""Discover the amendment timeline of a norm via BCN's (undocumented, but
public — the LeyChile web app itself calls it) `get_versiones` service.

Confirmed live (2026-08-02) against idNorma=206396 (Ley 19846). Shape:

    {
      "Versiones": {
        "@id_norma": 206396,
        "Version": [
          {
            "@tipoVersion": "Última Versión" | "Intermedio" | "Texto Original",
            "@vigenteDesde": "2022-12-30",
            "@vigenteHasta": "2022-12-29",   # absent on the newest entry
            "UrlVersion": {"$": "https://www.leychile.cl/N?i=206396&f=2022-12-30"},
            "Modificatorias": {              # absent on the newest entry
              "Modificatoria": { ... } | [ { ... }, { ... } ]
            }
          },
          ...
        ]
      }
    }

`Version` entries are windows of validity. Each window's `Modificatorias`
describes the amending norm(s) that ENDED that window (i.e. whose
`inicioVigencia` equals the *next* window's `vigenteDesde`) — so to build a
commit for version window N, look at window N-1's `Modificatorias`. The
oldest window ("Texto Original") has no predecessor: it's the norm's
original publication, with no amending norm.
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

# BCN's literal sentinel for "vigencia diferida por evento": an amendment
# whose effective date depends on a future regulation/event that hasn't
# happened yet, so BCN has no real date to give and uses this placeholder
# instead (confirmed live e.g. for Código de Aguas, idNorma=5605:
# tipoVersion="Con Vigencia Diferida por Evento", vigenteDesde="2222-02-02",
# no Modificatorias). This is distinct from a genuine *scheduled* future
# change (tipoVersion="Con Vigencia Diferida por Fecha" with a real date,
# e.g. "2027-02-25") which IS real and stays in the timeline. Also: git
# itself rejects this date outright ("fatal: invalid date format") so it
# couldn't become a commit even if it were meaningful to try.
BCN_DEFERRED_EVENT_SENTINEL = date(2222, 2, 2)


@dataclass(frozen=True)
class Modificatoria:
    id_norma: int
    nro_norma: str  # usually digits, but can be "S/N" for old un-numbered decrees
    tipo_norma: str
    titulo: str
    organismo: str
    fecha_publicacion: date
    inicio_vigencia: date


@dataclass(frozen=True)
class VersionEvent:
    """One commit's worth of state: the norm's text as of `vigente_desde`.

    `modificatorias` is the amending norm(s) that produced this version -
    empty either for the true original publication (`is_original`) or for
    an "unknown cause" gap (`is_unknown_cause`): BCN's own get_versiones
    data has real gaps scattered throughout every norm's history (verified
    live e.g. for Código de Comercio: entries at 1865-11-24, 2001-09-27,
    2005-11-24, 2021-04-13 all lack a recorded Modificatorias block despite
    not being the original). Only BCN's own `@tipoVersion == "Texto
    Original"` flag reliably identifies the true original - inferring it
    from "no modificatorias" produces false positives on every such gap.
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
    # get_versiones uses ISO (YYYY-MM-DD) for @vigenteDesde/@vigenteHasta/
    # @inicioVigencia, but "DD-MMM-YYYY" Spanish abbreviations (e.g.
    # "30-DIC-2022") for @fechaPublicacion. Handle both.
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
    block = version_raw.get("Modificatorias")
    if not block:
        return ()
    raw_mods = block["Modificatoria"]
    if isinstance(raw_mods, list):
        return tuple(_parse_modificatoria(m) for m in raw_mods)
    return (_parse_modificatoria(raw_mods),)


def fetch_version_timeline(client: BcnClient, id_norma: int) -> list[VersionEvent]:
    """Return every historical version window for `id_norma`, oldest first,
    each carrying the amending norm(s) that produced it (empty for the
    original publication)."""
    url = GET_VERSIONES_URL_TEMPLATE.format(id_norma=id_norma)
    result = client.get(url)
    data = json.loads(result.text())
    raw_versions = data["Versiones"]["Version"]
    if isinstance(raw_versions, dict):
        raw_versions = [raw_versions]

    # BCN lists these newest-first; each window's Modificatorias describes
    # what ENDED it, i.e. what produced the *next* (newer) window. Shift
    # accordingly so each VersionEvent carries the change that produced it.
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
            continue  # not a real date - see BCN_DEFERRED_EVENT_SENTINEL
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
