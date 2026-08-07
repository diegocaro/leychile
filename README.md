# leyes-chile

El **pipeline** que construye la historia git de la legislación chilena (la
Constitución y los 15 Códigos oficiales), a partir exclusivamente de los datos
públicos de [LeyChile (BCN)](https://www.leychile.cl). Sin LLMs y sin
transcripción manual: todo el texto sale de los servicios de la Biblioteca del
Congreso Nacional.

Este repositorio contiene **sólo la herramienta**. El producto final —los
`leyes/*.md` y la historia de commits que *es* su historia de modificaciones—
se escribe en un repositorio vecino, `../leychile-texto/` (constante
`DATA_REPO_ROOT` en `build_repo.py`), que debe existir y ser un repositorio git
antes de ejecutar el pipeline. En *este* repositorio nunca vas a encontrar el
texto de las leyes ni sus commits.

La idea: cada commit es una modificación real a una norma, con su fecha de
vigencia real, la ley que la modificó y quienes la firmaron. Así,
`git log` sobre un archivo es la historia legislativa de esa norma, y
`git diff` entre dos commits muestra exactamente qué cambió en el texto.

## Qué hay acá

- `src/leychile/`: el pipeline. Cada módulo tiene un docstring que explica
  qué endpoint de BCN usa y cómo se verificó su comportamiento.
- `config/targets.yaml`: las normas que se siguen (slug + `idNorma` de BCN),
  generado por `discover.py`.
- `cache/` (ignorado por git): todas las respuestas HTTP descargadas, cacheadas
  para siempre por URL. Reejecutar el pipeline nunca vuelve a pedir una URL ya
  descargada.
- `state.json` (ignorado por git): punto de control para reanudar. Registra qué
  pares `(idNorma, fecha de versión)` ya se commitearon **en `../leychile-texto/`**.

## Cómo ejecutarlo

Las dependencias se manejan con [uv](https://docs.astral.sh/uv/):

```bash
uv sync                       # crea el entorno e instala dependencias

# el repositorio de datos debe existir antes de construir nada
mkdir -p ../leychile-texto && git -C ../leychile-texto init

# (re)genera config/targets.yaml desde el catálogo oficial de BCN
uv run python -m leychile.discover

# construye o continúa la historia de commits en ../leychile-texto
# se puede detener y relanzar cuando sea
uv run python -m leychile.build_repo
```

**BCN limita las peticiones de forma agresiva** (durante el desarrollo
aparecieron `HTTP 429` tras unas pocas peticiones seguidas). Por eso
`build_repo` espera 6 segundos entre peticiones reales y cachea todo en disco.
Construir la historia completa de normas modificadas cientos de veces a lo
largo de más de un siglo toma su tiempo: conviene dejarlo corriendo en segundo
plano y aprovechar que `state.json` permite reanudar en varias sesiones.

## Auditabilidad

Cada archivo generado lleva un bloque de front-matter YAML que declara la URL
exacta de BCN, el `idNorma`, la fecha de la versión y el momento de la
descarga:

```yaml
---
source_url: https://nuevo.leychile.cl/servicios/Navegar/get_norma_json?idNorma=...&idVersion=...
id_norma: 172986
version_date: 2015-03-11
fetched_at: 2026-08-02T23:40:02.989982+00:00
titulo_norma: "..."
compuesto: LEY-...
organismos: [...]
fecha_publicacion_original: ...
---
```

El mensaje de cada commit también nombra la norma modificatoria y repite la URL
fuente de ese snapshot, así que cualquier commit se puede verificar de forma
independiente volviendo a consultar la API de BCN.

## De dónde salen los datos

La API pública *documentada* de BCN
(`leychile.cl/esquemas/accesoLeyesChilenas4.pdf`) resultó no servir para esto:
su parámetro `fechaVersion`, que en teoría entrega una versión histórica, es
ignorado silenciosamente y siempre devuelve el texto vigente. Se comprobó
pidiendo tres fechas distintas —incluida una anterior a cualquier
modificación— y recibiendo respuestas byte a byte idénticas.

El pipeline usa, en cambio, los servicios JSON internos de la propia aplicación
web de LeyChile. No están documentados públicamente, pero son públicos y del
mismo origen; se descubrieron abriendo leychile.cl en un navegador real e
inspeccionando sus peticiones de red:

| Para qué | Endpoint |
|---|---|
| Línea de tiempo de modificaciones de una norma | `nuevo.leychile.cl/servicios/Consulta/get_versiones?idNorma=<id>` |
| Texto tal como regía en una fecha | `nuevo.leychile.cl/servicios/Navegar/get_norma_json?idNorma=<id>&idVersion=<fecha>` |
| Parlamentarios autores (si los hay) | `nuevo.leychile.cl/servicios/Navegar/get_autores_de_la_ley?idNorma=<id>` |
| Catálogo oficial de los 15 Códigos | `nuevo.leychile.cl/servicios/Consulta/getCodigos` |

`get_norma_json` se verificó al revés que el endpoint documentado: tres fechas
distintas devolvieron tres snapshots genuinamente diferentes y de tamaño
creciente (38,8 KB → 41,7 KB → 41,8 KB), coincidiendo con el historial de
modificaciones conocido. Es decir, el parámetro sí se respeta.

## Autoría de los commits

Se combinan dos fuentes reales, para acreditar tanto a quien escribió la norma
como a quien la firmó:

- **Parlamentarios autores**, desde `get_autores_de_la_ley`. Sólo existen para
  las leyes que nacieron como moción parlamentaria.
- **Firmantes de la promulgación** (`src/leychile/promulgacion.py`),
  extraídos del propio texto de la norma: toda norma termina con un bloque de
  firmas con el Presidente (o, en las normas de 1973-1990, la Junta de
  Gobierno) y un ministro. Por ejemplo, el cierre real del Código Aeronáutico:
  `AUGUSTO PINOCHET UGARTE, Capitán General, Presidente de la República.- Hugo
  Rosende Subiabre, Ministro de Justicia`. A diferencia del endpoint anterior,
  este dato casi siempre está.

El autor principal del commit es el primer parlamentario autor si existe; si
no, el firmante principal de la promulgación; y si tampoco, el organismo
emisor. Cuando BCN directamente no registra qué norma causó un cambio (unos 250
de más de mil), no se atribuye a nadie: el autor es un marcador neutro, "Norma
modificatoria no registrada". Deducir quién encabezaba el Ejecutivo en esa fecha
sería inventar un dato que la fuente no tiene. Todos los demás (el resto de los autores, y los firmantes cuando el
autor principal fue un parlamentario) se agregan como líneas
`Co-authored-by:`, que es el formato que GitHub usa para mostrar varios
autores en un mismo commit. Así, una ley de moción acredita a sus diputados
*y* al Presidente que la firmó, en el mismo commit.

No existe un correo público real para legisladores y autoridades históricas, así
que se usa una dirección claramente sintética
(`...@sourced-from-bcn.leychile.invalid`): el objetivo es la atribución y la
auditoría, no tener un buzón que funcione.
