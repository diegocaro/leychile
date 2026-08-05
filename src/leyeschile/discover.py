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
CONSTITUCION_TITULO = "Constitución Política de la República de Chile"
CONSTITUCION_SLUG = "constitucion-politica-de-la-republica-de-chile"

# BCN no modela la historia constitucional chilena como una sola norma, sino
# como una cadena de normas distintas: cada nuevo texto constitucional (y cada
# decreto que "fija el texto") tiene su propio idNorma con su propia línea de
# tiempo. Seguir sólo la última (el texto refundido de 2005) deja fuera todo lo
# anterior a esa fecha.
#
# Este linaje reconstruye la historia completa. Cada eslabón fue verificado en
# vivo (2026-08-04) contra `get_versiones`, y los rangos son los que BCN
# efectivamente tiene con texto versionado:
#
#   137535  1833-05-25 -> 1888-08-10   8 versiones
#   241203  1971-10-25 -> 1977-03-12  13 versiones
#     7129  1980-08-11 -> 2005-08-26  21 versiones
#   242302  2005-09-22 -> presente    58 versiones
#
# Advertencia sobre la cobertura: BCN no tiene texto versionado para todo el
# período. Quedan huecos entre 1888 y 1971 (la Constitución de 1925 sólo
# aparece versionada desde 1971) y entre 1977 y 1980. No es un error del
# pipeline: es hasta donde llega la fuente.
CONSTITUCION_LINAJE = [
    {"id_norma": 137535, "nombre": "Constitución de 1833"},
    {"id_norma": 241203, "nombre": "Constitución de 1925 (DTO 1333, fija su texto)"},
    {"id_norma": 7129, "nombre": "Constitución de 1980 (DL 3464)"},
    {"id_norma": 242302, "nombre": "Texto refundido de 2005 (DTO 100)"},
]


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


def build_targets_yaml(client: BcnClient) -> list[dict]:
    """El corpus completo, en la forma que espera `config/targets.yaml`.

    La Constitución se escribe como un `linaje` (varias normas encadenadas en
    un solo archivo); los Códigos, como una sola `id_norma` cada uno.
    """
    entries: list[dict] = [
        {
            "slug": CONSTITUCION_SLUG,
            "note": CONSTITUCION_TITULO,
            "linaje": CONSTITUCION_LINAJE,
        }
    ]
    entries.extend(
        {"slug": t.slug, "id_norma": t.id_norma, "note": t.titulo} for t in fetch_codigos(client)
    )
    return entries


def main() -> None:
    import yaml

    from .build_repo import TARGETS_FILE

    client = BcnClient()
    entries = build_targets_yaml(client)
    TARGETS_FILE.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False))
    print(f"Se escribieron {len(entries)} normas en {TARGETS_FILE}")


if __name__ == "__main__":
    main()
