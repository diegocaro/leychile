# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Nota: este proyecto está documentado en español. Mantén los comentarios,
docstrings y documentación en español; los identificadores del código
(nombres de funciones, clases y variables) están en inglés/híbrido y se
mantienen así.

## Regla principal: comprobar, no deducir

**Toda explicación sobre el comportamiento de los datos debe verificarse contra
BCN antes de afirmarla o de escribirla en el código.** No basta con que una
hipótesis sea plausible y calce con lo observado.

Cómo comprobar, de más barato a más caro:

1. Consultar el endpoint directamente y mirar la respuesta cruda.
2. Contrastar con otra fuente de BCN: `get_versiones`, `getTiposNorma`,
   `metadatos` de `get_norma_json`.
3. **Abrir la ficha de la norma en el sitio web de BCN con el navegador
   Chrome** (herramientas `mcp__claude-in-chrome__*`), en
   `https://www.bcn.cl/leychile/navegar?idNorma=<id>` y, si se investiga una
   versión concreta, agregando `&idVersion=<AAAA-MM-DD>`.

   El sitio es la fuente de verdad visible: muestra fechas, versiones,
   estructura y la norma modificatoria de cada versión. **Ante cualquier duda
   sobre los datos, abrir Chrome y mirar la ficha**, no seguir deduciendo.

Si algo no se pudo comprobar, decirlo explícitamente ("no verificado") en vez
de presentarlo como hecho.

**Un endpoint que no trae un dato no significa que BCN no lo tenga.** La web
usa varios servicios distintos; inspeccionar sus peticiones de red (o
simplemente leer la ficha) revela cuál tiene lo que falta. Así se descubrió
que `get_ultima_modificatoria` sí atribuye versiones que `get_versiones` deja
sin causa.

Esta regla existe porque deducir sin comprobar ya produjo errores reales:

- Al derivar la lista de presidentes desde el corpus faltaba Frei Ruiz-Tagle.
  Se explicó como que "sus ministros firmaban como vicepresidentes" —plausible
  y con un dato que parecía apoyarlo— y se dio por cerrado. La causa real era
  un bug: la expresión regular de firmantes no aceptaba guiones, así que
  descartaba en silencio todo apellido compuesto. Frei desaparecía de las 39
  normas que firmó.
- La explicación de por qué una norma volvía sin texto (vacancia legal) se
  escribió como hecho antes de comprobarla. Resultó correcta, pero eso no se
  sabía al afirmarla; la ficha de BCN lo confirmó después ("Publicación:
  09-AGO-2025, Versión: Única - 02-MAR-2026").

## Qué es esto

Un pipeline que construye la historia git de la legislación chilena (la
Constitución y los 15 Códigos oficiales) a partir de los datos públicos de
LeyChile (BCN), sin LLMs ni transcripción manual.

Este repositorio es **sólo la herramienta**. La salida se escribe en un
repositorio vecino, `../leychile-texto` (`DATA_REPO_ROOT` en `build_repo.py`), que
debe existir y ser un repositorio git (`git init`) antes de ejecutar el
pipeline. Nunca busques el texto de las normas ni sus commits en *este*
repositorio.

El repositorio de datos se organiza en tres carpetas:

- `constitucion/`: la Constitución, aparte por ser jerárquicamente distinta.
- `codigos/`: los 15 códigos oficiales.
- `normas/<tipo>/`: las normas que modificaron a los anteriores (leyes,
  decretos leyes, autos acordados, sentencias...), en subcarpetas según el
  catálogo oficial de tipos de BCN.

Cada commit contiene a la vez el **efecto** (el código modificado) y la
**causa** (el texto de la norma que lo modificó), y enlaza la norma tanto a su
archivo en el repo como a BCN.

Ver `PENDIENTES.md` para ideas ya evaluadas y postergadas, con sus costos
medidos (sobre todo: darle historia de versiones propia a cada norma
modificatoria).

## Comandos

Las dependencias se manejan con `uv`:

```bash
uv sync                                    # crea el entorno e instala dependencias

# (re)genera config/targets.yaml desde el catálogo oficial de BCN
uv run python -m leychile.discover

# construye o continúa la historia de commits en ../leychile-texto
# es reanudable: se puede cortar (Ctrl-C / kill) y relanzar
uv run python -m leychile.build_repo

# sondear endpoints a mano contra una norma chica y conocida
# (Ley 19.846, idNorma=206396) antes de confiar en un supuesto nuevo
uv run python scripts/explore_api.py
```

No hay suite de tests, linter ni build más allá de `uv sync`. Tampoco hay CLI
más allá de esos dos módulos: para probar etapas sueltas del pipeline, usa
`uv run python -c "..."` importando directamente la función y llamándola con
una URL real (cacheada o en vivo).

## Arquitectura

**Etapas del pipeline** (`src/leychile/`). Cada módulo tiene un docstring que
detalla el endpoint de BCN que usa y cómo se verificó su comportamiento contra
datos reales. Lee ese docstring antes de tocar la lógica de parseo: varios de
estos servicios se descubrieron por ingeniería inversa desde la web de
LeyChile, no desde la documentación pública (que resultó no soportar la
descarga de versiones históricas).

1. `discover.py`: resuelve el `idNorma` de la Constitución y de los 15 Códigos
   vía el catálogo `getCodigos` de BCN (autoritativo, sin adivinar) más una
   búsqueda de texto libre como respaldo. Escribe `config/targets.yaml`.
2. `versions.py`: `get_versiones` → la línea de tiempo completa de una norma
   como lista de `VersionEvent` (una por ventana de vigencia, de la más antigua
   a la más nueva), cada una con la(s) norma(s) que la produjeron.
3. `norma_json.py`: `get_norma_json?idVersion=<fecha>` → el texto de la norma
   tal como regía en esa fecha, parseado desde un árbol de fragmentos HTML
   dentro de JSON hacia un `NormaDocument`.
4. `render.py`: `NormaDocument` → Markdown con un bloque de front-matter de
   auditoría (URL fuente, `idNorma`, fecha de versión, momento de descarga),
   para que cada archivo y cada commit se pueda verificar contra BCN.
4b. `tipos_norma.py`: catálogo oficial de tipos de norma (`getTiposNorma`, 39
   entradas). Traduce las siglas (`AA` → "Auto Acordado", `SEN` → "Sentencia"),
   define la carpeta de cada tipo dentro de `normas/`, y decide el verbo del
   mensaje de commit: una sentencia del TC *deroga parte de* un código, una
   rectificación *rectifica el texto de*, una ley *modifica*.
5. `promulgacion.py` + `signers.py`: resuelven la autoría real del commit.
   Parlamentarios autores (`get_autores_de_la_ley`, sólo poblado en leyes que
   nacieron como moción) más/o el Presidente —o la Junta de Gobierno— y el
   ministro que la firmaron, extraídos del texto de promulgación de la propia
   norma. Cuando hay ambas fuentes se combinan: un autor principal y el resto
   como líneas `Co-authored-by:` (`Author.trailer()`).
6. `build_repo.py`: el orquestador. Mezcla las líneas de tiempo de todas las
   normas en **una sola lista global ordenada por fecha** (así la historia git
   queda como una cronología real entrelazada entre documentos, no agrupada
   norma por norma) y crea un commit por evento en `DATA_REPO_ROOT`.
7. `client.py`: la capa HTTP por la que pasa todo lo anterior. Cachea cada
   respuesta en disco para siempre indexada por URL (`cache/`, ignorado por
   git), con backoff que respeta `Retry-After` ante 429/5xx. **Nunca hagas
   llamadas directas con `requests` saltándote este cliente**: BCN limita las
   peticiones de forma agresiva (aparecieron 429 tras unas pocas peticiones
   durante el desarrollo), y además la caché es lo que hace barato reconstruir
   todo el repositorio después de corregir un bug de parseo (no se vuelve a
   descargar nada, sólo se re-parsea).

**Reanudabilidad**: `state.json` (ignorado por git, en la raíz) registra qué
eventos `"<id_norma>:<vigente_desde>"` ya están commiteados en `../leychile-texto`.
`build_repo.main()` los salta y reintenta los que fallaron en corridas
anteriores. El fallo de un evento nunca aborta la corrida completa (ver el
try/except alrededor de `commit_event` en `main()`).

## Trampas no obvias de los datos de BCN

Cada una de éstas se descubrió chocando con inconsistencias reales a mitad de
construcción, no leyendo documentación. Si vas a tocar `versions.py` o el
manejo de fechas de `build_repo.py`, relee esta lista:

- **git rechaza fechas de commit anteriores al 1970-01-01** (verificado contra
  git 2.50.1). Las modificaciones reales del Código Civil (1855), el Código de
  Comercio (1865), etc. son anteriores. Solución ya implementada:
  `assign_commit_datetimes()` fija esos commits en `1970-01-01T00:00:00Z` más
  un segundo por cada evento, en orden cronológico real, para que el orden
  relativo sobreviva en `git log`. La fecha real siempre queda en el mensaje
  del commit y en el front-matter.
- **El bloque `Modificatorias` a veces falta en ventanas que no son la
  original**: BCN tiene huecos reales en sus propios registros, repartidos por
  la historia de cada código. Sólo `@tipoVersion == "Texto Original"`
  identifica de forma confiable la publicación original
  (`VersionEvent.is_original`); una ventana sin causa registrada que *no* sea
  la original es `is_unknown_cause`, y recibe un mensaje de commit honesto
  ("norma modificatoria no registrada por BCN") en vez de quedar mal etiquetada
  como una segunda "publicación original".
- **`2222-02-02` es un centinela literal de BCN**, no una fecha real: marca
  "vigencia diferida por evento" (la entrada en vigor depende de un reglamento
  futuro que aún no se publica). `versions.py` los filtra por completo
  (`BCN_DEFERRED_EVENT_SENTINEL`). No confundir con cambios futuros realmente
  programados (`tipoVersion == "Con Vigencia Diferida por Fecha"` con fecha
  real, como `2027-02-25`), que sí son reales y se conservan.
- **El endpoint documentado ignora el parámetro de versión**: en
  `leychile.cl/Consulta/obtxml`, `fechaVersion` se acepta pero no tiene ningún
  efecto, y siempre devuelve el texto vigente. No recurras a él para funciones
  de versiones históricas. Los servicios JSON no documentados de
  `nuevo.leychile.cl` usados en `versions.py`/`norma_json.py` son los que sí
  funcionan.
