"""Resuelve el idNorma de la Constitución y de los Códigos.

Dos endpoints de BCN, ambos verificados en vivo (2026-08-02) y descubiertos
inspeccionando las peticiones de red de leychile.cl en un navegador real:

- `getCodigos`: el listado curado por la propia BCN con los 15 Códigos
  oficiales chilenos, cada uno con su idNorma. Es la fuente autoritativa, así
  que no hace falta adivinar ningún número.
- `buscarjson?cadena=<consulta>`: búsqueda de texto libre, para resolver lo que
  no esté en `getCodigos`. La Constitución no es un "Código", así que no
  aparece en ese listado. Verificado con "Constitución Política de la
  República": idNorma=242302, "Decreto 100 - FIJA EL TEXTO REFUNDIDO,
  COORDINADO Y SISTEMATIZADO DE LA CONSTITUCION POLITICA DE LA REPUBLICA DE
  CHILE".

Ese decreto es el texto consolidado y vigente. Es el patrón habitual de BCN: el
decreto que "fija el texto refundido" es el documento vivo, igual que el Código
Civil, que está "contenido en el DFL 1 ... que fija su texto refundido".
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

# getCodigos no devuelve la Constitución (no es un "Código"), así que se
# resolvió aparte y queda fijada acá una vez verificada en vivo. Ver el
# docstring del módulo para el detalle de cómo se comprobó.
CONSTITUCION_ID_NORMA = 242302
CONSTITUCION_TITULO = "Constitución Política de la República de Chile"


@dataclass(frozen=True)
class DiscoveredTarget:
    """Documento a seguir: su nombre de archivo, su idNorma y su título."""

    slug: str
    id_norma: int
    titulo: str


def _slugify(text: str) -> str:
    """Convierte un título en nombre de archivo: sin tildes, en minúsculas y
    con guiones (p. ej. "Código Civil" -> "codigo-civil")."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if (c.isalnum() and ord(c) < 128) or c in " -")
    ascii_text = re.sub(r"\s+", "-", ascii_text.strip())
    return re.sub(r"-+", "-", ascii_text)


def fetch_codigos(client: BcnClient) -> list[DiscoveredTarget]:
    """Los 15 Códigos oficiales, según el catálogo propio de BCN."""
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
    """Búsqueda de texto libre. Devuelve los diccionarios crudos del resultado
    (IDNORMA, NORMA, TITULO_NORMA, ...) para inspeccionarlos y desambiguar a
    mano; es la herramienta para agregar normas nuevas a `targets.yaml`."""
    url = BUSCAR_URL_TEMPLATE.format(query=urllib.parse.quote(query), n=limit)
    result = client.get(url)
    data = json.loads(result.text())
    return data[0] if data else []


def build_target_list(client: BcnClient) -> list[DiscoveredTarget]:
    """El corpus completo: la Constitución más todos los Códigos oficiales."""
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
    print(f"Se escribieron {len(targets)} normas en {TARGETS_FILE}")


if __name__ == "__main__":
    main()
