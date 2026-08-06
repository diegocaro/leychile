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
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .client import BcnClient
from .norma_json import NormaDocument, parse_norma_json
from .render import render_markdown, render_markdown_norma
from .signers import Author, autores_sin_registro, resolve_authors
from .tipos_norma import TipoNorma, describir, fetch_catalogo, verbo_para
from .versions import Modificatoria, VersionEvent, fetch_version_timeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGETS_FILE = REPO_ROOT / "config" / "targets.yaml"
STATE_FILE = REPO_ROOT / "state.json"

DATA_REPO_ROOT = REPO_ROOT.parent / "leychile"

# El repositorio de datos se organiza en tres carpetas, según qué es cada cosa:
#
#   constitucion/  la Constitución (un solo archivo, con su linaje completo).
#                  Va aparte de los códigos porque es jerárquicamente distinta.
#   codigos/       los 15 códigos oficiales.
#   normas/<tipo>/ las normas que modificaron a los anteriores (leyes, decretos
#                  leyes, autos acordados, sentencias...), en subcarpetas por
#                  tipo según el catálogo oficial de BCN (ver tipos_norma.py).
CONSTITUCION_DIR = DATA_REPO_ROOT / "constitucion"
CODIGOS_DIR = DATA_REPO_ROOT / "codigos"
NORMAS_DIR = DATA_REPO_ROOT / "normas"

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


@dataclass(frozen=True)
class LineageLink:
    """Un eslabón del linaje de un documento: una norma de BCN con su rango
    propio de versiones."""

    id_norma: int
    nombre: str = ""


@dataclass
class Target:
    """Documento que seguimos: un archivo del repo construido a partir de uno o
    más eslabones.

    La mayoría de los documentos tiene un solo eslabón. La Constitución tiene
    cuatro, porque BCN no modela la historia constitucional como una sola
    norma sino como una cadena de normas distintas (ver
    `discover.CONSTITUCION_LINAJE`): seguir sólo la última dejaría la historia
    empezando en el texto refundido de 2005.
    """

    slug: str
    links: list[LineageLink]
    categoria: str = "codigo"  # "codigo" | "constitucion"

    @property
    def tiene_linaje(self) -> bool:
        return len(self.links) > 1

    @property
    def path(self) -> Path:
        base = CONSTITUCION_DIR if self.categoria == "constitucion" else CODIGOS_DIR
        return base / f"{self.slug}.md"


@dataclass
class CommitEvent:
    """Un commit por hacer: un documento en una de sus versiones históricas.

    `link` indica de qué norma de BCN sale este texto, que en un documento con
    linaje no es siempre la misma.
    """

    target: Target
    link: LineageLink
    version: VersionEvent

    @property
    def key(self) -> str:
        """Identificador estable del evento, usado en `state.json` para saber
        qué ya se commiteó y poder reanudar."""
        return f"{self.link.id_norma}:{self.version.vigente_desde.isoformat()}"


def load_targets() -> list[Target]:
    """Lee `targets.yaml`, que acepta dos formas por entrada: `id_norma` para
    un documento simple, o `linaje` para uno encadenado."""
    raw = yaml.safe_load(TARGETS_FILE.read_text())
    targets: list[Target] = []
    for t in raw:
        if t.get("linaje"):
            links = [
                LineageLink(id_norma=int(e["id_norma"]), nombre=e.get("nombre", ""))
                for e in t["linaje"]
            ]
        else:
            links = [LineageLink(id_norma=int(t["id_norma"]))]
        targets.append(
            Target(slug=t["slug"], links=links, categoria=t.get("categoria", "codigo"))
        )
    return targets


# Tipos con numeración nacional correlativa: el número nunca se reinicia, así
# que identifica la norma por sí solo (la Ley 21.522 es única para siempre).
# Verificado en el corpus: 613 leyes y 52 decretos leyes numerados, sin una sola
# colisión.
#
# El resto de los tipos reinicia la numeración cada año y por organismo, así que
# el número solo no distingue nada: hay dos "DFL 1" del Ministerio de Justicia
# que son normas completamente distintas (el de 1992 corrige el Código de
# Justicia Militar; el de 1995 fija el texto refundido de la Ley 19.366 sobre
# estupefacientes), y dos "AA 21" de la Corte Suprema que ella misma titula
# "ACTA N° 21-2018" y "ACTA N° 21-2020", con el año en el nombre oficial.
TIPOS_NUMERACION_CORRELATIVA = {"LEY", "DL"}


def ruta_de_norma(mod: Modificatoria, tipo: TipoNorma) -> Path:
    """Dónde se guarda una norma modificatoria dentro de `normas/<tipo>/`.

    El nombre depende de cómo se numera ese tipo de norma:

    - Numeración correlativa nacional (LEY, DL): `LEY-21522.md`. El número basta.
    - Numeración que se reinicia cada año (DFL, AA, DTO...): `DFL-1995-0001.md`,
      con el año por delante.
    - Normas sin número ("S/N", 28 en el corpus: leyes del siglo XIX, autos
      acordados antiguos, sentencias y avisos): `LEY-SN-1874-08-13-131717.md`.
      El `SN` va inmediatamente después del tipo para que no quede nada en la
      posición del número: `LEY-1874-...` se leía como "Ley 1874", que además
      existe como número real. Después va la fecha completa y al final el
      `idNorma`, porque ni siquiera la fecha desempata: el 13 de agosto de 1874
      se publicaron dos leyes sin número (131717 y 131718).

    Ver `TIPOS_NUMERACION_CORRELATIVA` para por qué la distinción es de fondo y
    no un accidente del corpus actual.

    Dos detalles de formato, ambos para que listar la carpeta ya venga ordenado
    de forma útil:

    - **El año va antes del número**, cuando corresponde. Con NUMERO-AÑO,
      `DFL-00001-1995` quedaría antes que `DFL-00002-1980`, mezclando épocas.
    - **El número se rellena con ceros**, para que el orden alfabético coincida
      con el numérico (sin relleno, `LEY-19506` queda antes que `LEY-9506`,
      porque compara caracteres y no valores). El ancho depende del tipo de
      numeración: **5 dígitos** en las correlativas, que acumulan más de un
      siglo de normas y ya van por los 21.000; **4 dígitos** en las que se
      reinician cada año, donde el número parte de cero en enero y no alcanza
      esas magnitudes.
    """
    numero_crudo = str(mod.nro_norma).strip()
    fecha = mod.fecha_publicacion or mod.inicio_vigencia
    anio = fecha.year
    if not numero_crudo.isdigit():
        return NORMAS_DIR / tipo.slug / f"{tipo.abbr}-SN-{fecha.isoformat()}-{mod.id_norma}.md"
    numero = int(numero_crudo)
    if tipo.abbr in TIPOS_NUMERACION_CORRELATIVA:
        return NORMAS_DIR / tipo.slug / f"{tipo.abbr}-{numero:05d}.md"
    return NORMAS_DIR / tipo.slug / f"{tipo.abbr}-{anio}-{numero:04d}.md"


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
    participantes: list[Author],
    co_authors: list[Author],
    catalogo: dict[str, TipoNorma],
    ruta_documento: Path,
    rutas_normas: dict[int, Path] | None = None,
    inicio_de_eslabon: LineageLink | None = None,
) -> str:
    """Mensaje del commit: qué cambió, cuándo rigió y de qué URL de BCN salió.

    El cuerpo siempre cita la fuente exacta para que el commit sea auditable, y
    termina con las líneas `Co-authored-by:` que GitHub usa para mostrar a
    todos los autores y firmantes.

    El verbo depende del tipo de norma (ver `tipos_norma.verbo_para`): una
    sentencia del Tribunal Constitucional no "modifica" un código, deroga la
    parte declarada inconstitucional; una rectificación corrige una errata del
    Diario Oficial.

    `rutas_normas` mapea idNorma -> archivo guardado en el repo, para enlazar la
    norma modificatoria además de citar su URL en BCN.

    `inicio_de_eslabon` se pasa cuando este commit estrena un eslabón del
    linaje, para advertir que el diff gigante es un cambio de cuerpo legal y no
    una reforma puntual.

    El **asunto** conserva la descripción legible y agrega al final el archivo
    de la norma modificatoria, para poder ubicarla o buscarla directamente
    (`git log --grep=LEY-21522`) sin abrir el cuerpo del commit.
    """
    rutas_normas = rutas_normas or {}
    # Los títulos de BCN traen saltos de línea incrustados; el asunto del commit
    # tiene que ser una sola línea.
    titulo_norma = " ".join((titulo_norma or "").split())

    def _describe(m: Modificatoria) -> str:
        """Nombre legible de la norma: "Ley 18750", "Auto Acordado 21".

        Las normas sin número lo dicen explícitamente. Antes quedaban sólo como
        "Ley", que no distingue nada: hay 27 en el corpus, ocho de ellas leyes.
        """
        tipo = describir(catalogo, m.tipo_norma)
        if str(m.nro_norma) == "S/N":
            return f"{tipo.nombre} sin número"
        return f"{tipo.nombre} {m.nro_norma}"

    def _nombre_archivo(m: Modificatoria) -> str:
        """Identificador corto de la norma: el nombre de su archivo sin `.md`."""
        ruta = rutas_normas.get(m.id_norma)
        if ruta is not None:
            return ruta.stem
        return ruta_de_norma(m, describir(catalogo, m.tipo_norma)).stem

    # Asunto: "[IDENTIFICADOR] AAAA-MM-DD: título".
    #
    # El identificador y el título son los de la NORMA MODIFICATORIA, no los del
    # documento afectado: cuál documento cambió ya se ve en el diff, y su título
    # sería idéntico en todos sus commits, mientras que el de la norma dice qué
    # hizo esa reforma en particular.
    #
    # La fecha va en el asunto porque la de git no sirve para orientarse: todo
    # lo anterior a 1970 aparece como 1970-01-01 (ver GIT_EPOCH), justo el tramo
    # más difícil de seguir.
    if inicio_de_eslabon is not None:
        identificador = "LINAJE"
        titulo = inicio_de_eslabon.nombre or titulo_norma
    elif version.is_original:
        identificador = "ORIGINAL"
        titulo = titulo_norma
    elif version.is_unknown_cause:
        # Sin norma registrada no hay identificador ni título propio: se usa el
        # del documento, que es lo único que se sabe.
        identificador = "SIN-REGISTRO"
        titulo = titulo_norma
    else:
        # Cuando varias normas rigen desde la misma fecha, el asunto nombra a la
        # más relevante y no a la que BCN listó primero. Se usa el campo `otro`
        # de su propio catálogo, que separa normas principales (ley, decreto,
        # DFL, decreto ley) de instrumentos accesorios (rectificación, aviso,
        # auto acordado, sentencia).
        #
        # Sin esto, el 1999-09-17 el asunto decía "RECTIFICACIÓN" —que no
        # informa nada— en vez de la Ley 19.617, que fue la que modificó el
        # Código Penal ese día.
        ordenadas = sorted(
            version.modificatorias, key=lambda m: not describir(catalogo, m.tipo_norma).principal
        )
        principal = ordenadas[0]
        identificador = _nombre_archivo(principal)
        if len(ordenadas) > 1:
            identificador = f"{identificador} +{len(ordenadas) - 1}"
        titulo = " ".join((principal.titulo or "").split()) or titulo_norma

    header = f"[{identificador}] {version.vigente_desde.isoformat()}: {titulo}"

    lines = [
        header,
        "",
        f"Documento: {ruta_documento}",
        f"Fecha de vigencia: {version.vigente_desde.isoformat()}",
        f"Fuente: {source_url}",
    ]
    for m in version.modificatorias:
        lines.append("")
        # El verbo distingue qué hizo realmente esta norma: una sentencia del
        # Tribunal Constitucional deroga, una rectificación corrige una errata.
        # No se repite la sigla del tipo: el nombre ya la dice ("Ley (LEY)") y
        # además va en el identificador del archivo, más abajo.
        lines.append(
            f"{_describe(m)} {verbo_para(m.tipo_norma)} el documento "
            f"— {m.organismo or 'organismo no registrado'}"
        )
        if m.titulo:
            lines.append(f"  {' '.join(m.titulo.split())}")
        ruta = rutas_normas.get(m.id_norma)
        if ruta is not None:
            lines.append(f"  Texto en este repo: {ruta}")
        lines.append(f"  En BCN: https://www.leychile.cl/Navegar?idNorma={m.id_norma}")
    if inicio_de_eslabon is not None:
        lines.append(
            "\nNota: este commit estrena un nuevo cuerpo legal dentro del linaje del "
            "documento, así que el diff refleja el reemplazo completo del texto anterior "
            "y no una reforma puntual."
        )
    if date_clamped:
        lines.append(
            "Nota: fecha de commit git sintética (git no admite fechas anteriores a "
            "1970-01-01); la fecha de vigencia real es la indicada arriba."
        )

    # Quién participó y en qué calidad. Va en una sección propia porque el
    # trailer `Co-authored-by:` tiene formato fijo y no admite el cargo. Los
    # roles de los firmantes son los textuales de la promulgación.
    con_rol = [p for p in participantes if p.rol]
    if con_rol:
        lines.append("")
        lines.append("Participantes:")
        lines.extend(f"  {p.con_rol()}" for p in con_rol)
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


def _guardar_norma(
    client: BcnClient, mod: Modificatoria, catalogo: dict[str, TipoNorma]
) -> tuple[Path | None, NormaDocument | None]:
    """Guarda el texto de una norma modificatoria en `normas/<tipo>/`.

    Devuelve (ruta relativa al repo de datos, documento) para poder enlazarla en
    el mensaje del commit y reutilizar el documento al resolver los firmantes,
    sin descargarlo dos veces. Si la descarga falla devuelve (None, None): tener
    el texto de la norma es deseable, pero nunca debe impedir el commit.
    """
    tipo = describir(catalogo, mod.tipo_norma)
    # El texto se pide con la fecha de la norma misma, no con la de su efecto
    # sobre el documento modificado (ver Modificatoria.fecha_de_su_texto).
    fecha_texto = mod.fecha_de_su_texto.isoformat()
    try:
        url = GET_NORMA_JSON_URL_TEMPLATE.format(id_norma=mod.id_norma, version_date=fecha_texto)
        result = client.get(url)
        doc = parse_norma_json(result.content, id_norma=mod.id_norma, version_date=fecha_texto)

        # Una norma publicada con vacancia legal existe en el Diario Oficial
        # antes de regir, pero BCN sólo tiene su texto desde la primera ventana
        # de vigencia: pedirla en su fecha de publicación devuelve un documento
        # vacío. Pasaba en 57 de las 714 normas del corpus (la Ley 21.759 se
        # publicó el 2025-08-09 y su texto sólo existe desde el 2026-03-02).
        #
        # La respuesta vacía sí trae `metadatos.vigencias`, así que se reintenta
        # con la ventana más antigua, que es el texto original de la norma.
        if doc.sin_texto and doc.vigencias:
            fecha_texto = min(doc.vigencias)
            url = GET_NORMA_JSON_URL_TEMPLATE.format(id_norma=mod.id_norma, version_date=fecha_texto)
            result = client.get(url)
            doc = parse_norma_json(result.content, id_norma=mod.id_norma, version_date=fecha_texto)
    except Exception:  # noqa: BLE001 - ver docstring
        return None, None

    ruta = ruta_de_norma(mod, tipo)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        render_markdown_norma(
            doc,
            source_url=url,
            fetched_at=result.fetched_at,
            tipo_abbr=tipo.abbr,
            tipo_nombre=tipo.nombre,
            numero=str(mod.nro_norma),
            organismo=mod.organismo,
            fecha_vigencia=mod.inicio_vigencia.isoformat(),
            fecha_publicacion=mod.fecha_publicacion.isoformat() if mod.fecha_publicacion else "",
        )
    )
    return ruta.relative_to(DATA_REPO_ROOT), doc


def commit_event(
    client: BcnClient,
    event: CommitEvent,
    commit_dt: str,
    catalogo: dict[str, TipoNorma],
    *,
    inicio_de_eslabon: LineageLink | None = None,
) -> None:
    """Descarga, renderiza y commitea una versión de un documento.

    El texto se pide siempre a `event.link.id_norma`, que en un documento con
    linaje no coincide con "la norma del target": va cambiando de eslabón a
    medida que avanza la historia.

    En el mismo commit se guardan también las normas que causaron el cambio, de
    modo que un commit contenga a la vez el efecto (el código modificado) y la
    causa (la ley que lo modificó).
    """
    target, version, link = event.target, event.version, event.link
    url = GET_NORMA_JSON_URL_TEMPLATE.format(
        id_norma=link.id_norma, version_date=version.vigente_desde.isoformat()
    )
    result = client.get(url)
    doc = parse_norma_json(result.content, id_norma=link.id_norma, version_date=version.vigente_desde.isoformat())
    markdown = render_markdown(doc, source_url=url, fetched_at=result.fetched_at)

    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_text(markdown)
    archivos = [target.path]

    # Guardar el texto de cada norma modificatoria junto al documento afectado.
    rutas_normas: dict[int, Path] = {}
    docs_normas: dict[int, NormaDocument] = {}
    for mod in version.modificatorias:
        ruta, doc_norma = _guardar_norma(client, mod, catalogo)
        if ruta is not None:
            rutas_normas[mod.id_norma] = ruta
            archivos.append(DATA_REPO_ROOT / ruta)
        if doc_norma is not None:
            docs_normas[mod.id_norma] = doc_norma

    organismo = doc.organismos[0] if doc.organismos else "Congreso Nacional de Chile"
    if version.is_original:
        # El propio documento ya trae su Promulgación (la firma de su
        # publicación original), así que no hace falta descargar nada más.
        resolved = resolve_authors(client, id_norma=link.id_norma, organismo=organismo, promulgacion_doc=doc)
    elif version.is_unknown_cause:
        # BCN no registra qué norma causó esta transición, así que no hay a
        # quién atribuirla: ver el comentario de AUTOR_SIN_REGISTRO.
        resolved = autores_sin_registro()
    else:
        # Los firmantes salen del texto de la norma modificatoria, que ya
        # descargamos y guardamos arriba.
        primary_mod = version.modificatorias[0]
        resolved = resolve_authors(
            client,
            id_norma=primary_mod.id_norma,
            organismo=primary_mod.organismo,
            promulgacion_doc=docs_normas.get(primary_mod.id_norma),
        )

    date_clamped = version.vigente_desde < GIT_EPOCH
    message = build_commit_message(
        version,
        url,
        doc.titulo_norma,
        date_clamped=date_clamped,
        participantes=resolved.participantes,
        co_authors=resolved.co_authors,
        catalogo=catalogo,
        ruta_documento=target.path.relative_to(DATA_REPO_ROOT),
        rutas_normas=rutas_normas,
        inicio_de_eslabon=inicio_de_eslabon,
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

    for archivo in archivos:
        git("add", str(archivo.relative_to(DATA_REPO_ROOT)))
    git("commit", "-m", message, env=env)


MARCA_INDICE = "<!-- INDICE-GENERADO -->"


def generar_indice(targets: list[Target], catalogo: dict[str, TipoNorma]) -> str:
    """Índice navegable del repo de datos, generado a partir de lo que hay en
    disco (no de lo que creemos que debería haber).

    Se inserta en el README bajo la marca `MARCA_INDICE`, reemplazando todo lo
    que venga después, para poder regenerarlo en cada build sin pisar el texto
    escrito a mano.
    """
    lineas = [MARCA_INDICE, "", "## Índice", ""]

    consti = [t for t in targets if t.categoria == "constitucion" and t.path.exists()]
    if consti:
        lineas.append("### Constitución")
        lineas.append("")
        for t in consti:
            lineas.append(f"- [{t.slug}]({t.path.relative_to(DATA_REPO_ROOT)})")
        lineas.append("")

    codigos = sorted(
        (t for t in targets if t.categoria == "codigo" and t.path.exists()), key=lambda t: t.slug
    )
    if codigos:
        lineas += ["### Códigos", "", "| Código | Versiones en el repo |", "|---|---|"]
        for t in codigos:
            rel = t.path.relative_to(DATA_REPO_ROOT)
            lineas.append(f"| [{t.slug}]({rel}) | ver `git log -- {rel}` |")
        lineas.append("")

    if NORMAS_DIR.exists():
        lineas += ["### Normas modificatorias", "", "| Tipo | Cantidad | Carpeta |", "|---|---|---|"]
        por_nombre = {t.slug: t for t in catalogo.values()}
        for carpeta in sorted(NORMAS_DIR.iterdir()):
            if not carpeta.is_dir():
                continue
            cantidad = len(list(carpeta.glob("*.md")))
            tipo = por_nombre.get(carpeta.name)
            nombre = tipo.nombre if tipo else carpeta.name
            lineas.append(f"| {nombre} | {cantidad} | [`normas/{carpeta.name}/`](normas/{carpeta.name}/) |")
        lineas.append("")

    return "\n".join(lineas)


def escribir_indice(targets: list[Target], catalogo: dict[str, TipoNorma]) -> Path | None:
    """Reescribe la sección de índice del README del repo de datos."""
    readme = DATA_REPO_ROOT / "README.md"
    if not readme.exists():
        return None
    contenido = readme.read_text()
    cabecera = contenido.split(MARCA_INDICE)[0].rstrip()
    readme.write_text(f"{cabecera}\n\n{generar_indice(targets, catalogo)}")
    return readme


def main() -> None:
    if not (DATA_REPO_ROOT / ".git").exists():
        raise SystemExit(
            f"{DATA_REPO_ROOT} no es un repositorio git. Créalo primero, por ejemplo:\n"
            f"  mkdir -p {DATA_REPO_ROOT} && git -C {DATA_REPO_ROOT} init"
        )

    targets = load_targets()
    client = BcnClient(min_delay_seconds=6.0)
    done = load_state()
    catalogo = fetch_catalogo(client)

    # Primero se arma la línea de tiempo completa de todas las normas, y recién
    # después se commitea: el orden cronológico es global, así que no se puede
    # ir commiteando norma por norma a medida que se descargan.
    all_events: list[CommitEvent] = []
    for target in targets:
        for link in target.links:
            timeline = fetch_version_timeline(client, link.id_norma)
            for version in timeline:
                all_events.append(CommitEvent(target=target, link=link, version=version))

    all_events.sort(key=lambda e: (e.version.vigente_desde, e.target.slug))
    commit_datetimes = assign_commit_datetimes(all_events)

    # En documentos con linaje, marcamos el primer evento de cada eslabón (salvo
    # el más antiguo) para que su commit advierta que el diff es un cambio de
    # cuerpo legal completo. Se calcula sobre la lista ya ordenada, así que
    # refleja el orden cronológico real.
    inicios_de_eslabon: dict[str, LineageLink] = {}
    for target in targets:
        if not target.tiene_linaje:
            continue
        eventos_del_target = [e for e in all_events if e.target is target]
        eslabones_vistos: set[int] = set()
        for evento in eventos_del_target:
            if evento.link.id_norma in eslabones_vistos:
                continue
            eslabones_vistos.add(evento.link.id_norma)
            if len(eslabones_vistos) > 1:  # el primero es el inicio natural, no un salto
                inicios_de_eslabon[evento.key] = evento.link

    committed_this_run = 0
    failed: list[str] = []
    for event in all_events:
        if event.key in done:
            continue
        print(f"Commiteando {event.key} ({event.target.slug})...", file=sys.stderr)
        try:
            commit_event(
                client,
                event,
                commit_datetimes[event.key],
                catalogo,
                inicio_de_eslabon=inicios_de_eslabon.get(event.key),
            )
        except Exception as exc:  # noqa: BLE001 - un evento malo no puede matar un recorrido de días
            print(f"  FALLÓ {event.key}: {exc!r} (se reintentará en la próxima corrida)", file=sys.stderr)
            failed.append(event.key)
            continue
        done.add(event.key)
        save_state(done)
        committed_this_run += 1

    # El índice se regenera al final, cuando ya están todos los archivos en
    # disco, y se commitea aparte: describe el repo, no es historia legal.
    readme = escribir_indice(targets, catalogo)
    if readme is not None:
        subprocess.run(["git", "add", "README.md"], cwd=DATA_REPO_ROOT, check=True)
        hay_cambios = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=DATA_REPO_ROOT
        ).returncode != 0
        if hay_cambios:
            subprocess.run(
                ["git", "commit", "-m", "Actualiza el índice del README"], cwd=DATA_REPO_ROOT, check=True
            )

    print(
        f"Listo. {len(all_events)} versiones en total, {len(done)} commiteadas acumuladas "
        f"({committed_this_run} en esta corrida, {len(failed)} fallidas que se reintentarán).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
