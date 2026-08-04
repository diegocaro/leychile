"""Determina la autoría del commit git de cada modificación.

Se combinan dos fuentes de datos reales e independientes, para acreditar tanto
a "quien la escribió" como a "quien la firmó", cuando ambos datos existen:

- `get_autores_de_la_ley?idNorma=<id>` (verificado en vivo el 2026-08-02):
  lista JSON de parlamentarios autores. Sólo viene poblada en normas que
  nacieron como moción parlamentaria (p. ej. idNorma=1063104 / Ley 20.756 ->
  10 diputados con nombre). Viene vacía en los proyectos de iniciativa
  presidencial (mensajes).
- Firmantes de la promulgación (`promulgacion.py`), extraídos del propio texto
  de la norma: el Presidente (o miembro de la Junta de Gobierno) y el ministro
  que la firmaron. A diferencia del endpoint anterior, casi siempre están.

Prioridad: si hay parlamentarios autores, el primero es el autor principal del
commit, porque "quién la escribió" es el crédito más específico; los firmantes
de la promulgación se agregan como líneas `Co-authored-by:`. Si no hay autores
con nombre, el firmante principal de la promulgación (Presidente/Junta) pasa a
ser el autor principal y el ministro queda como coautor. Si ninguna fuente
tiene datos, se cae al `organismo` emisor.

Git exige un par "Nombre <email>" para el autor. No existe un correo público
real para legisladores y autoridades históricas, así que se usa una dirección
claramente sintética: el objetivo es la atribución y la auditoría, no tener un
buzón que funcione.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field

from .client import BcnClient
from .norma_json import NormaDocument
from .promulgacion import Signer, extract_signers, minister_signer, primary_signer

GET_AUTORES_URL_TEMPLATE = "https://nuevo.leychile.cl/servicios/Navegar/get_autores_de_la_ley?idNorma={id_norma}"

PLACEHOLDER_EMAIL_DOMAIN = "sourced-from-bcn.leychile.invalid"
MAX_NAMED_CO_AUTHORS = 6  # hay mociones con más de una docena de autores; limitamos la lista


@dataclass(frozen=True)
class Author:
    name: str
    email: str

    def trailer(self) -> str:
        """Línea `Co-authored-by:`, el formato que GitHub reconoce para
        mostrar varios autores en un mismo commit."""
        return f"Co-authored-by: {self.name} <{self.email}>"


@dataclass(frozen=True)
class ResolvedAuthors:
    """Autor principal del commit más sus coautores."""

    primary: Author
    co_authors: list[Author] = field(default_factory=list)


def _slugify_for_email(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if (c.isalnum() and ord(c) < 128) or c in " -")
    return "-".join(ascii_text.split()) or "bcn"


def _author_from_name(name: str) -> Author:
    return Author(name=name, email=f"{_slugify_for_email(name)}@{PLACEHOLDER_EMAIL_DOMAIN}")


def _author_from_signer(signer: Signer) -> Author:
    # Las firmas del Presidente y de la Junta suelen venir en mayúsculas; las
    # pasamos a formato título para que el nombre del autor se vea normal en
    # git. Sólo afecta a Author.name, no al texto de Signer.role que se usa en
    # otras partes (p. ej. en los mensajes de commit).
    name = signer.name.title() if signer.name.isupper() else signer.name
    return _author_from_name(name)


def fetch_named_authors(client: BcnClient, id_norma: int) -> list[str]:
    url = GET_AUTORES_URL_TEMPLATE.format(id_norma=id_norma)
    result = client.get(url)
    data = json.loads(result.text())
    return [entry["n"] for entry in data if entry.get("n")]


def organismo_only_authors(organismo: str) -> ResolvedAuthors:
    """Para las transiciones donde BCN no registra qué norma las causó (ver
    `versions.VersionEvent.is_unknown_cause`): no hay ningún id_norma que
    consultar para obtener autores ni firmantes, así que se va directo al
    respaldo por organismo."""
    organismo_clean = organismo.strip() or "Congreso Nacional de Chile"
    return ResolvedAuthors(primary=_author_from_name(organismo_clean.title()))


def resolve_authors(
    client: BcnClient, *, id_norma: int, organismo: str, promulgacion_doc: NormaDocument | None
) -> ResolvedAuthors:
    """Autoría del commit según la prioridad descrita en el docstring del módulo."""
    named = fetch_named_authors(client, id_norma)
    signers = extract_signers(promulgacion_doc) if promulgacion_doc is not None else []
    pres = primary_signer(signers)
    minister = minister_signer(signers, exclude=pres)

    co_authors: list[Author] = []

    if named:
        primary = _author_from_name(named[0])
        for extra_name in named[1 : MAX_NAMED_CO_AUTHORS + 1]:
            co_authors.append(_author_from_name(extra_name))
        if pres is not None:
            co_authors.append(_author_from_signer(pres))
        if minister is not None:
            co_authors.append(_author_from_signer(minister))
    elif pres is not None:
        primary = _author_from_signer(pres)
        if minister is not None:
            co_authors.append(_author_from_signer(minister))
    else:
        organismo_clean = organismo.strip() or "Congreso Nacional de Chile"
        primary = _author_from_name(organismo_clean.title())

    return ResolvedAuthors(primary=primary, co_authors=co_authors)
