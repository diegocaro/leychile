"""Resolve idNorma for the Constitution + major Codes corpus.

Two BCN endpoints confirmed live (2026-08-02), both discovered by driving a
real browser to leychile.cl and inspecting its network calls:

- `getCodigos` — BCN's own curated list of the 15 official Chilean Códigos,
  each with a confirmed idNorma. This is authoritative; no guessing needed.
- `buscarjson?cadena=<query>` — free-text search, used to resolve anything
  not in `getCodigos` (the Constitution isn't a "Código" so it's absent from
  that list). Verified against "Constitución Política de la República":
  idNorma=242302, "Decreto 100 - FIJA EL TEXTO REFUNDIDO, COORDINADO Y
  SISTEMATIZADO DE LA CONSTITUCION POLITICA DE LA REPUBLICA DE CHILE" — this
  is the up-to-date consolidated text (BCN's standard pattern: the "texto
  refundido" decree is the living document, same as e.g. Código Civil being
  "contenido en el DFL 1 ... que fija su texto refundido").
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass

from .client import BcnClient

GET_CODIGOS_URL = "https://nuevo.leychile.cl/servicios/Consulta/getCodigos"
BUSCAR_URL_TEMPLATE = "https://nuevo.leychile.cl/servicios/buscarjson?cadena={query}&itemsporpagina={n}&npagina=1"

# The Constitution isn't returned by getCodigos (it's not a "Código"), so
# it's resolved separately and pinned here once confirmed live - see
# module docstring for how this was verified.
CONSTITUCION_ID_NORMA = 242302
CONSTITUCION_TITULO = "Constitución Política de la República de Chile"


@dataclass(frozen=True)
class DiscoveredTarget:
    slug: str
    id_norma: int
    titulo: str


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if (c.isalnum() and ord(c) < 128) or c in " -")
    ascii_text = re.sub(r"\s+", "-", ascii_text.strip())
    return re.sub(r"-+", "-", ascii_text)


def fetch_codigos(client: BcnClient) -> list[DiscoveredTarget]:
    result = client.get(GET_CODIGOS_URL)
    data = json.loads(result.text())
    targets = []
    for entry in data:
        titulo = entry["titulo"].strip()
        targets.append(
            DiscoveredTarget(slug=_slugify(titulo), id_norma=int(entry["idNorma"]), titulo=titulo)
        )
    return targets


def search_norma(client: BcnClient, query: str, *, limit: int = 10) -> list[dict]:
    """Free-text search; returns raw result dicts (IDNORMA, NORMA,
    TITULO_NORMA, ...) for manual inspection/disambiguation."""
    url = BUSCAR_URL_TEMPLATE.format(query=urllib.parse.quote(query), n=limit)
    result = client.get(url)
    data = json.loads(result.text())
    return data[0] if data else []


def build_target_list(client: BcnClient) -> list[DiscoveredTarget]:
    """Constitution + every official Código."""
    targets = [DiscoveredTarget(slug=_slugify(CONSTITUCION_TITULO), id_norma=CONSTITUCION_ID_NORMA, titulo=CONSTITUCION_TITULO)]
    targets.extend(fetch_codigos(client))
    return targets


def main() -> None:
    import yaml

    from .build_repo import TARGETS_FILE

    client = BcnClient()
    targets = build_target_list(client)
    raw = [{"slug": t.slug, "id_norma": t.id_norma, "note": t.titulo} for t in targets]
    TARGETS_FILE.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    print(f"Wrote {len(targets)} targets to {TARGETS_FILE}")


if __name__ == "__main__":
    main()
