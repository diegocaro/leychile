"""Orquestador: construye la historia git de las normas a partir de LeyChile.

Para cada norma listada en `config/targets.yaml`:

1. Obtiene su línea de tiempo completa de versiones (`versions.py`).
2. Para cada ventana de vigencia, descarga el texto tal como regía en esa fecha
   exacta (`norma_json.py`) y lo renderiza a Markdown (`render.py`).
3. Crea un commit con la fecha de vigencia real y la autoría resuelta
   (`signers.py`).

Todos los eventos de todas las normas se mezclan en **una sola lista global
ordenada por fecha** antes de empezar a hacer commits. Así la historia
resultante es una línea de tiempo cronológica real y entrelazada entre todos los
documentos, en vez de quedar agrupada norma por norma.

Reanudable: el progreso se guarda en `state.json` (ignorado por git), con
claves "<id_norma>:<vigente_desde>". Los eventos ya commiteados se saltan, así
que el script se puede detener y relanzar sin problema durante un recorrido
largo y limitado por el rate limit de BCN.

Dos repositorios separados, por decisión de diseño: este repositorio
(leyes-chile) es sólo la herramienta (código, caché HTTP, state.json). El
producto final —los `leyes/*.md` y su historia de commits— vive en el
repositorio vecino `DATA_REPO_ROOT`, para poder publicar y compartir la historia
legal de forma independiente del código que la genera.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .client import BcnClient
from .norma_json import NormaDocument, parse_norma_json
from .render import render_markdown
from .signers import Author, organismo_only_authors, resolve_authors
from .versions import VersionEvent, fetch_version_timeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGETS_FILE = REPO_ROOT / "config" / "targets.yaml"
STATE_FILE = REPO_ROOT / "state.json"

DATA_REPO_ROOT = REPO_ROOT.parent / "leychile"
LAWS_DIR = DATA_REPO_ROOT / "leyes"

# git rechaza de plano cualquier fecha de commit anterior a la época Unix
# (verificado en vivo contra git 2.50.1: "fatal: invalid date format", tanto en
# formato ISO como en segundos @epoch). Pero las modificaciones reales del
# Código Civil (1855), el Código de Comercio (1865), el Código Penal (1875),
# etc. son todas anteriores a 1970.
#
# Decisión del proyecto: a esos commits se les fija la fecha git en
# 1970-01-01T00:00:00Z, desplazando un segundo por cada evento (en orden
# cronológico real) para que el orden relativo se conserve en `git log`. La
# fecha histórica real nunca se pierde: siempre está en el mensaje del commit y
# en el front-matter del archivo.
GIT_EPOCH = date(1970, 1, 1)

GET_NORMA_JSON_URL_TEMPLATE = (
    "https://nuevo.leychile.cl/servicios/Navegar/get_norma_json"
    "?idNorma={id_norma}&idVersion={version_date}&idLey=&tipoVersion=&cve=&agrupa_partes=1&r="
)


@dataclass
class Target:
    """Norma que seguimos: nombre de archivo en `leyes/` y su idNorma en BCN."""

    slug: str
    id_norma: int


@dataclass
class CommitEvent:
    """Un commit por hacer: una norma en una de sus versiones históricas."""

    target: Target
    version: VersionEvent

    @property
    def key(self) -> str:
        """Identificador estable del evento, usado en `state.json` para saber
        qué ya se commiteó y poder reanudar."""
        return f"{self.target.id_norma}:{self.version.vigente_desde.isoformat()}"


def load_targets() -> list[Target]:
    raw = yaml.safe_load(TARGETS_FILE.read_text())
    return [Target(slug=t["slug"], id_norma=int(t["id_norma"])) for t in raw]


def load_state() -> set[str]:
    """Claves de los eventos ya commiteados (vacío en la primera ejecución)."""
    if not STATE_FILE.exists():
        return set()
    return set(json.loads(STATE_FILE.read_text()))


def save_state(done: set[str]) -> None:
    # Se guarda después de cada commit, no al final: si el proceso muere a
    # media construcción, lo ya hecho no se repite en la siguiente corrida.
    STATE_FILE.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=2))


def build_commit_message(
    version: VersionEvent,
    source_url: str,
    titulo_norma: str,
    *,
    date_clamped: bool,
    co_authors: list[Author],
) -> str:
    """Mensaje del commit: qué cambió, cuándo rigió y de qué URL de BCN salió.

    El cuerpo siempre cita la fuente exacta para que el commit sea auditable, y
    termina con las líneas `Co-authored-by:` que GitHub usa para mostrar a
    todos los autores y firmantes.
    """
    if version.is_original:
        header = f"Publicación original: {titulo_norma}"
    elif version.is_unknown_cause:
        header = f"Nueva versión de {titulo_norma} (norma modificatoria no registrada por BCN)"
    else:
        mods_desc = "; ".join(f"{m.tipo_norma} {m.nro_norma} ({m.organismo})" for m in version.modificatorias)
        header = f"{mods_desc} modifica {titulo_norma}"
    lines = [header, "", f"Fecha de vigencia: {version.vigente_desde.isoformat()}", f"Fuente: {source_url}"]
    for m in version.modificatorias:
        lines.append(f"Norma modificatoria: https://www.leychile.cl/Navegar?idNorma={m.id_norma}")
    if date_clamped:
        lines.append(
            "Nota: fecha de commit git sintética (git no admite fechas anteriores a "
            "1970-01-01); la fecha de vigencia real es la indicada arriba."
        )
    if co_authors:
        lines.append("")
        lines.extend(a.trailer() for a in co_authors)
    return "\n".join(lines)


def assign_commit_datetimes(events: list["CommitEvent"]) -> dict[str, str]:
    """Fecha git de cada commit: la fecha real de vigencia (mediodía, hora de
    Chile).

    Excepción: los eventos anteriores a `GIT_EPOCH` reciben una fecha sintética
    que parte en 1970-01-01T00:00:00Z y avanza un segundo por cada evento de
    ese tipo, siguiendo el orden de `events` (que se asume ya ordenado
    cronológicamente a nivel global). Ver el comentario de `GIT_EPOCH`.
    """
    result: dict[str, str] = {}
    pre_epoch_counter = 0
    for event in events:
        d = event.version.vigente_desde
        if d < GIT_EPOCH:
            synthetic = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=pre_epoch_counter)
            result[event.key] = synthetic.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            pre_epoch_counter += 1
        else:
            result[event.key] = f"{d.isoformat()} 12:00:00 -0300"
    return result


def git(*args: str, env: dict[str, str] | None = None) -> None:
    """Ejecuta git *siempre* en el repositorio de datos, nunca en éste."""
    subprocess.run(["git", *args], cwd=DATA_REPO_ROOT, check=True, env=env)


def _fetch_norma_doc(client: BcnClient, id_norma: int, version_date: str) -> NormaDocument:
    """Descarga y parsea una norma tal como regía en `version_date`."""
    url = GET_NORMA_JSON_URL_TEMPLATE.format(id_norma=id_norma, version_date=version_date)
    result = client.get(url)
    return parse_norma_json(result.content, id_norma=id_norma, version_date=version_date)


def commit_event(client: BcnClient, event: CommitEvent, commit_dt: str) -> None:
    """Descarga, renderiza y commitea una versión de una norma."""
    target, version = event.target, event.version
    url = GET_NORMA_JSON_URL_TEMPLATE.format(
        id_norma=target.id_norma, version_date=version.vigente_desde.isoformat()
    )
    result = client.get(url)
    doc = parse_norma_json(result.content, id_norma=target.id_norma, version_date=version.vigente_desde.isoformat())
    markdown = render_markdown(doc, source_url=url, fetched_at=result.fetched_at)

    LAWS_DIR.mkdir(parents=True, exist_ok=True)
    law_path = LAWS_DIR / f"{target.slug}.md"
    law_path.write_text(markdown)

    organismo = doc.organismos[0] if doc.organismos else "Congreso Nacional de Chile"
    if version.is_original:
        # El propio documento ya trae su Promulgación (la firma de su
        # publicación original), así que no hace falta descargar nada más.
        resolved = resolve_authors(client, id_norma=target.id_norma, organismo=organismo, promulgacion_doc=doc)
    elif version.is_unknown_cause:
        # BCN no registra qué norma causó esta transición: no hay id_norma que
        # consultar para autores ni firmantes, así que no se puede ser más
        # específico que el organismo.
        resolved = organismo_only_authors(organismo)
    else:
        # Para saber quién firmó la modificación hay que ir al texto de la
        # norma modificatoria, que es un documento distinto.
        primary_mod = version.modificatorias[0]
        try:
            amending_doc = _fetch_norma_doc(client, primary_mod.id_norma, primary_mod.inicio_vigencia.isoformat())
        except Exception:  # noqa: BLE001 - el firmante es "si se puede"; nunca debe frenar el commit
            amending_doc = None
        resolved = resolve_authors(
            client, id_norma=primary_mod.id_norma, organismo=primary_mod.organismo, promulgacion_doc=amending_doc
        )

    date_clamped = version.vigente_desde < GIT_EPOCH
    message = build_commit_message(
        version, url, doc.titulo_norma, date_clamped=date_clamped, co_authors=resolved.co_authors
    )

    author = resolved.primary
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": author.name,
            "GIT_AUTHOR_EMAIL": author.email,
            "GIT_AUTHOR_DATE": commit_dt,
            "GIT_COMMITTER_NAME": author.name,
            "GIT_COMMITTER_EMAIL": author.email,
            "GIT_COMMITTER_DATE": commit_dt,
        }
    )

    git("add", str(law_path.relative_to(DATA_REPO_ROOT)))
    git("commit", "-m", message, env=env)


def main() -> None:
    if not (DATA_REPO_ROOT / ".git").exists():
        raise SystemExit(
            f"{DATA_REPO_ROOT} no es un repositorio git. Créalo primero, por ejemplo:\n"
            f"  mkdir -p {DATA_REPO_ROOT} && git -C {DATA_REPO_ROOT} init"
        )

    targets = load_targets()
    client = BcnClient(min_delay_seconds=6.0)
    done = load_state()

    # Primero se arma la línea de tiempo completa de todas las normas, y recién
    # después se commitea: el orden cronológico es global, así que no se puede
    # ir commiteando norma por norma a medida que se descargan.
    all_events: list[CommitEvent] = []
    for target in targets:
        timeline = fetch_version_timeline(client, target.id_norma)
        for version in timeline:
            all_events.append(CommitEvent(target=target, version=version))

    all_events.sort(key=lambda e: (e.version.vigente_desde, e.target.slug))
    commit_datetimes = assign_commit_datetimes(all_events)

    committed_this_run = 0
    failed: list[str] = []
    for event in all_events:
        if event.key in done:
            continue
        print(f"Commiteando {event.key} ({event.target.slug})...", file=sys.stderr)
        try:
            commit_event(client, event, commit_datetimes[event.key])
        except Exception as exc:  # noqa: BLE001 - un evento malo no puede matar un recorrido de días
            print(f"  FALLÓ {event.key}: {exc!r} (se reintentará en la próxima corrida)", file=sys.stderr)
            failed.append(event.key)
            continue
        done.add(event.key)
        save_state(done)
        committed_this_run += 1

    print(
        f"Listo. {len(all_events)} versiones en total, {len(done)} commiteadas acumuladas "
        f"({committed_this_run} en esta corrida, {len(failed)} fallidas que se reintentarán).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
