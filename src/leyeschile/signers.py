"""Resolve git commit authorship for an amendment event.

Two independent real data sources are combined, per project decision, so
both "who wrote it" and "who signed it into law" are credited when available:

- `get_autores_de_la_ley?idNorma=<id>` (confirmed live 2026-08-02): JSON list
  of named congressional authors, populated only for norms that began as a
  parliamentary "moción" (e.g. idNorma=1063104 / Ley 20.756 -> 10 named
  deputies). Empty for presidential-message bills.
- Promulgación signers (promulgacion.py), parsed from the norm's own
  rendered text: the President/Junta member and a Minister who actually
  signed it into law. Almost always present, unlike get_autores_de_la_ley.

Priority: named congressional author (if any) is the primary git author,
since "who wrote this" is the more specific credit; the promulgación
signer(s) are added as `Co-authored-by:` trailers. If there are no named
congressional authors, the promulgación primary signer (President/Junta)
becomes the primary author instead, with the Minister as co-author. If
neither source has data, falls back to the issuing `organismo`.

Git commit authors need a "Name <email>" pair. There's no real public email
for historical legislators/officials, so a clearly-synthetic placeholder
address is used — the point is attribution/audit, not a working mailbox.
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
MAX_NAMED_CO_AUTHORS = 6  # some mociones have a dozen+ named authors; cap the trailer list


@dataclass(frozen=True)
class Author:
    name: str
    email: str

    def trailer(self) -> str:
        return f"Co-authored-by: {self.name} <{self.email}>"


@dataclass(frozen=True)
class ResolvedAuthors:
    primary: Author
    co_authors: list[Author] = field(default_factory=list)


def _slugify_for_email(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in normalized if (c.isalnum() and ord(c) < 128) or c in " -")
    return "-".join(ascii_text.split()) or "bcn"


def _author_from_name(name: str) -> Author:
    return Author(name=name, email=f"{_slugify_for_email(name)}@{PLACEHOLDER_EMAIL_DOMAIN}")


def _author_from_signer(signer: Signer) -> Author:
    # Junta/president signatures are often printed all-caps; title-case for
    # a normal-looking git author name. Only affects Author.name here, not
    # the Signer.role text used elsewhere (e.g. commit messages).
    name = signer.name.title() if signer.name.isupper() else signer.name
    return _author_from_name(name)


def fetch_named_authors(client: BcnClient, id_norma: int) -> list[str]:
    url = GET_AUTORES_URL_TEMPLATE.format(id_norma=id_norma)
    result = client.get(url)
    data = json.loads(result.text())
    return [entry["n"] for entry in data if entry.get("n")]


def organismo_only_authors(organismo: str) -> ResolvedAuthors:
    """For version transitions where BCN doesn't record which norm caused
    them (see versions.VersionEvent.is_unknown_cause) - no id_norma exists
    to look up named authors or a promulgación signer for, so this skips
    straight to the organismo fallback."""
    organismo_clean = organismo.strip() or "Congreso Nacional de Chile"
    return ResolvedAuthors(primary=_author_from_name(organismo_clean.title()))


def resolve_authors(
    client: BcnClient, *, id_norma: int, organismo: str, promulgacion_doc: NormaDocument | None
) -> ResolvedAuthors:
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
