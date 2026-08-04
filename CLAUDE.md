# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pipeline that builds a git history of Chilean law (Constitución + the 15
official Códigos) sourced entirely from BCN's LeyChile public data — no
LLMs, no manual transcription. This repo is **tooling only**. The output —
`leyes/*.md` and the commit history that *is* their amendment history — is
written into a separate sibling repo at `../leychile` (`DATA_REPO_ROOT` in
`build_repo.py`), which must already exist and be a git repo (`git init`)
before running the pipeline. Never look for law text or law commits in
*this* repo.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e .

# (re)build config/targets.yaml from BCN's canonical Código list + Constitution
.venv/bin/python -m leyeschile.discover

# build/continue the commit history in ../leychile - resumable, safe to
# stop (Ctrl-C / kill) and re-run any time
.venv/bin/python -m leyeschile.build_repo

# one-off endpoint probing/debugging against a known-good test norm
# (Ley 19846, idNorma=206396) before trusting a new assumption about a
# BCN response shape
.venv/bin/python scripts/explore_api.py
```

There is no test suite, linter, or build step beyond `pip install -e .`.
There's no `main` script/CLI beyond the two modules above; poke at
individual pipeline stages via `python -c` (see git history / prior debug
sessions for the pattern: `sys.path.insert(0, 'src')`, instantiate
`BcnClient`, call the function directly against a real cached or live URL).

## Architecture

**Pipeline stages** (`src/leyeschile/`), each with a docstring detailing
the exact BCN endpoint it hits and how that endpoint's behavior was
verified against real data — read the module docstring before changing
its parsing logic, since several of these were reverse-engineered from
undocumented internal JSON services (not BCN's public PDF-documented API,
which turned out not to support historical-version retrieval at all):

1. `discover.py` — resolves `idNorma` for the Constitution + all 15
   Códigos via BCN's `getCodigos` catalog (canonical, no guessing) and a
   search fallback; writes `config/targets.yaml`.
2. `versions.py` — `get_versiones` → the full amendment timeline for one
   norm as a list of `VersionEvent`s (one per historical version window,
   oldest first), each carrying the amending norm(s) that produced it.
3. `norma_json.py` — `get_norma_json?idVersion=<date>` → the norm's actual
   text as it read on that date, parsed from an HTML-fragment-in-JSON tree
   into a `NormaDocument`.
4. `render.py` — `NormaDocument` → Markdown with an audit front-matter
   block (source URL, `idNorma`, version date, fetch timestamp) so every
   generated file/commit is independently re-verifiable against BCN.
5. `promulgacion.py` + `signers.py` — resolve a real git commit author:
   named congressional authors (`get_autores_de_la_ley`, only populated
   for laws that began as a parliamentary "moción") plus/else the
   President-or-Junta and Minister who signed it into law, parsed straight
   out of the norm's own Promulgación text (`promulgacion.py`). Both are
   combined when available: one primary author, the rest as git
   `Co-authored-by:` trailers (`Author.trailer()`).
6. `build_repo.py` — the orchestrator. Merges every target's version
   timeline into one global, date-sorted event list (so the resulting git
   history is a true interleaved timeline across all documents, not
   grouped per-document) and creates one commit per event in
   `DATA_REPO_ROOT`.
7. `client.py` — the shared HTTP layer everything above goes through:
   disk-caches every response forever by URL under `cache/` (gitignored),
   with backoff honoring `Retry-After` on 429/5xx. **Never bypass this
   client with raw `requests` calls** — BCN rate-limits aggressively
   (observed 429s after a handful of requests during development), and the
   cache is also what makes rebuilds after a logic fix cheap (no
   re-fetching, just re-parsing).

**Resumability**: `state.json` (gitignored, repo root) tracks which
`"<id_norma>:<vigente_desde>"` events are already committed to
`../leychile`. `build_repo.main()` skips those and retries anything that
failed on a prior run. A single event's failure never aborts the run (see
the try/except around `commit_event` in `main()`).

## Non-obvious BCN data gotchas

These were each discovered by hitting real inconsistencies mid-build, not
from any documentation — if you touch `versions.py` or `build_repo.py`'s
date handling, re-read this list:

- **git refuses commit dates before 1970-01-01** (verified directly
  against git 2.50.1). Real amendments to Código Civil (1855), Código de
  Comercio (1865), etc. predate this. Fix in place:
  `assign_commit_datetimes()` clamps pre-1970 commits to
  `1970-01-01T00:00:00Z` plus one second per such event in true
  chronological order, so relative order survives in `git log` — the real
  date always stays in the commit message and file front-matter.
- **`Modificatorias` (the amending-norm data) is sometimes absent on a
  non-original version window**, not just the true original — BCN has
  real gaps in its own records scattered across every code's history.
  Only `@tipoVersion == "Texto Original"` reliably identifies the true
  original (`VersionEvent.is_original`); a window with no recorded cause
  that *isn't* the original is `is_unknown_cause`, and gets an honest
  "norma modificatoria no registrada por BCN" commit message instead of
  being mislabeled as a second "original".
- **`2222-02-02` is a literal BCN sentinel**, not a real date — it marks
  "vigencia diferida por evento" (effective date depends on a future
  regulation that hasn't been published yet). `versions.py` filters these
  out entirely (`BCN_DEFERRED_EVENT_SENTINEL`). Don't confuse this with
  genuine scheduled-future changes (`tipoVersion == "Con Vigencia
  Diferida por Fecha"` with a real date like `2027-02-25`), which are real
  and stay in the timeline.
- The documented public endpoint (`leychile.cl/Consulta/obtxml`,
  `fechaVersion` param) silently ignores the version-date parameter and
  always returns current text — don't reach for it when adding
  historical-version features. The undocumented `nuevo.leychile.cl`
  JSON services in `versions.py`/`norma_json.py` are what actually work
  and are what's in use.
