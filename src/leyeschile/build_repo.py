"""Orchestrator: build the git history of tracked laws from BCN LeyChile.

For every target in config/targets.yaml, pulls the full version timeline
(versions.py), and for every version window fetches the real point-in-time
text (norma_json.py), renders it (render.py), and creates one git commit
with the real effective date and a resolved author (signers.py).

All target's version-events are merged into one global, date-sorted list
before committing, so the resulting git history is a true interleaved
timeline across every tracked document, not grouped per-document.

Resumable: progress is tracked in state.json (gitignored) keyed by
"<id_norma>:<vigente_desde>"; already-committed events are skipped, so the
script can be stopped and restarted freely over a long, rate-limited crawl.

Two separate repos, per project decision: this repo (leyes-chile) is just
the tooling (scripts, HTTP cache, state.json). The actual deliverable -
leyes/*.md and their commit history - lives in a sibling repo, DATA_REPO_ROOT
below, so the law history can be published/shared independently of the
pipeline code.
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

# git hard-refuses commit dates before the Unix epoch (verified live against
# git 2.50.1: "fatal: invalid date format" for both ISO and raw @epoch-seconds
# forms of any pre-1970 date). Real amendments to Código Civil (1855), Código
# de Comercio (1865), Código Penal (1875), etc. all predate this. Per-project
# decision: clamp those commits' git date to 1970-01-01T00:00:00Z, offsetting
# by one second per such event (in true chronological order) so relative
# order is preserved in `git log` — the real historical date is never lost,
# it's always in the commit message and the file's front-matter.
GIT_EPOCH = date(1970, 1, 1)

GET_NORMA_JSON_URL_TEMPLATE = (
    "https://nuevo.leychile.cl/servicios/Navegar/get_norma_json"
    "?idNorma={id_norma}&idVersion={version_date}&idLey=&tipoVersion=&cve=&agrupa_partes=1&r="
)


@dataclass
class Target:
    slug: str
    id_norma: int


@dataclass
class CommitEvent:
    target: Target
    version: VersionEvent

    @property
    def key(self) -> str:
        return f"{self.target.id_norma}:{self.version.vigente_desde.isoformat()}"


def load_targets() -> list[Target]:
    raw = yaml.safe_load(TARGETS_FILE.read_text())
    return [Target(slug=t["slug"], id_norma=int(t["id_norma"])) for t in raw]


def load_state() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    return set(json.loads(STATE_FILE.read_text()))


def save_state(done: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=2))


def build_commit_message(
    version: VersionEvent,
    source_url: str,
    titulo_norma: str,
    *,
    date_clamped: bool,
    co_authors: list[Author],
) -> str:
    if version.is_original:
        header = f"Publicación original: {titulo_norma}"
    elif version.is_unknown_cause:
        header = f"Nueva versión de {titulo_norma} (norma modificatoria no registrada por BCN)"
    else:
        mods_desc = "; ".join(f"{m.tipo_norma} {m.nro_norma} ({m.organismo})" for m in version.modificatorias)
        header = f"{mods_desc} modifica {titulo_norma}"
    lines = [header, "", f"Fecha de vigencia: {version.vigente_desde.isoformat()}", f"Source: {source_url}"]
    for m in version.modificatorias:
        lines.append(f"Amending norm: https://www.leychile.cl/Navegar?idNorma={m.id_norma}")
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
    """Real date (noon, Chile time) as the git author/committer date, except
    events before GIT_EPOCH get a synthetic date starting at
    1970-01-01T00:00:00Z, incrementing by one second per such event in
    `events`' order (assumed already globally chronologically sorted)."""
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
    subprocess.run(["git", *args], cwd=DATA_REPO_ROOT, check=True, env=env)


def _fetch_norma_doc(client: BcnClient, id_norma: int, version_date: str) -> NormaDocument:
    url = GET_NORMA_JSON_URL_TEMPLATE.format(id_norma=id_norma, version_date=version_date)
    result = client.get(url)
    return parse_norma_json(result.content, id_norma=id_norma, version_date=version_date)


def commit_event(client: BcnClient, event: CommitEvent, commit_dt: str) -> None:
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
        # The target's own document already carries its own Promulgación
        # (its original enactment signature) - no extra fetch needed.
        resolved = resolve_authors(client, id_norma=target.id_norma, organismo=organismo, promulgacion_doc=doc)
    elif version.is_unknown_cause:
        # BCN doesn't record which norm caused this transition - no id_norma
        # to look up authors/signers for, so this can't be more specific.
        resolved = organismo_only_authors(organismo)
    else:
        primary_mod = version.modificatorias[0]
        try:
            amending_doc = _fetch_norma_doc(client, primary_mod.id_norma, primary_mod.inicio_vigencia.isoformat())
        except Exception:  # noqa: BLE001 - promulgación signer is best-effort, never block the commit
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
            f"{DATA_REPO_ROOT} is not a git repo. Create it first, e.g.:\n"
            f"  mkdir -p {DATA_REPO_ROOT} && git -C {DATA_REPO_ROOT} init"
        )

    targets = load_targets()
    client = BcnClient(min_delay_seconds=6.0)
    done = load_state()

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
        print(f"Committing {event.key} ({event.target.slug})...", file=sys.stderr)
        try:
            commit_event(client, event, commit_datetimes[event.key])
        except Exception as exc:  # noqa: BLE001 - a single bad event must not kill a multi-day crawl
            print(f"  FAILED {event.key}: {exc!r} (will retry on next run)", file=sys.stderr)
            failed.append(event.key)
            continue
        done.add(event.key)
        save_state(done)
        committed_this_run += 1

    print(
        f"Done. {len(all_events)} total version-events, {len(done)} committed overall "
        f"({committed_this_run} this run, {len(failed)} failed and will retry next run).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
