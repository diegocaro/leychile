# Issues

Defectos verificados y límites conocidos, con la evidencia que los respalda y el
comando para volver a comprobarlos. A diferencia de `PENDIENTES.md`, que guarda
ideas evaluadas y postergadas, acá va lo que está **mal** o lo que la fuente
**no permite**.

Estado: `abierto` · `en curso` · `cerrado` · `sin solución` (la fuente no lo
permite y no depende de nosotros).

Última verificación completa: **2026-08-17**, contra 988 commits de
`../leychile-texto`.

---

## A. Fidelidad de los datos: hasta dónde llega BCN

Resuelve la investigación que pedía `PENDIENTES.md` §3 («Linaje para los
códigos»), que decía que había que averiguar la cadena de antecesores código por
código. Conclusiones del 2026-08-17.

**La distinción que importa** está en la redacción del catálogo `getCodigos`:

- **«Contenido en X»** — Tributario, Sanitario, Justicia Militar, Aguas. El
  código *es* esa norma y el repo arranca en su publicación original. No falta
  historia por este motivo.
- **«*Actualmente* contenido en el DFL 1, que fija su *texto refundido*»** —
  Civil y Trabajo. El código es más antiguo que la norma que hoy lo contiene.

### A1. Código Civil anterior a 2000 — `sin solución`

BCN no publica el Código Civil previo al DFL 1 del año 2000 en ningún formato
que estos servicios entreguen. Comprobado por cuatro vías:

| vía | resultado |
|---|---|
| `get_versiones` sobre `172986` | 50 versiones, la primera 2000-05-30 marcada `Texto Original` |
| búsqueda de texto libre | ninguna norma versionada de 1855; sólo el «Mensaje del Ejecutivo» que propuso el código |
| los 4 decretos «aprueba texto oficial del Código Civil» (`15152` 1987, `73624` 1997, `210517` 2003, `232863` 2004) | ~2.000 caracteres cada uno, una sola versión, **sin el texto del código**: son sólo el acto aprobatorio |
| las 140 `NOTA:` dentro del texto del 2000 | todas sobre leyes posteriores a 2000 |

Consecuencia práctica: la historia del Código Civil en el repo empieza el
2000-05-30, y son 145 años que no están del lado nuestro.

### A2. Código del Trabajo anterior a 2003 — `sin solución`

La Ley 18.620 de 1987 tiene ficha (`idNorma=30011`), y su texto vía JSON
devuelve literalmente «Texto disponible solamente en formato PDF».

Extraer el PDF contradice la premisa del proyecto (sin transcripción) y
entregaría una foto única sin línea de tiempo.

### A3. Código de Aguas anterior a 1981 — `abierto`, **recuperable**

El hallazgo útil de la investigación. La **Ley 8.944 de 1948**
(`idNorma=125715`) contiene el Código de Aguas completo: 136 KB, artículos 1 al
359, texto estructurado. Una sola versión, del 1948-02-11.

El catálogo de BCN no lo advierte: llama al Código de Aguas «Contenido en el DFL
1.122 (1981)» sin mencionar que hubo un código anterior.

La mecánica para incorporarlo ya existe y es genérica. Es un cambio sólo de
`config/targets.yaml`, sin tocar código:

```yaml
- slug: codigo-de-aguas
  categoria: codigo
  linaje:
  - id_norma: 125715   # Código de Aguas de 1948 (Ley 8.944)
  - id_norma: 5605     # Código de Aguas de 1981 (DFL 1.122)
```

Ganaría un commit real y verificable más el diff del reemplazo de 1981. Una
foto, y aun así historia auténtica que hoy falta.

### A4. Predecesores de Justicia Militar y Sanitario — `abierto`, sin investigar

El caso de Aguas (A3) muestra que el catálogo calla los códigos anteriores. Los
mismos candidatos existen para Justicia Militar (repo desde 1944-12-19) y
Sanitario (repo desde 1968-01-31). Falta buscarlos con el método de A3:
`search_norma` con «APRUEBA CÓDIGO …» y revisar si la norma trae texto o sólo
PDF.

### A5. `codigo-civil.md` contiene seis leyes, no una — `abierto`

El DFL 1 del año 2000 es de **doble articulado**: además del Código Civil
(artículo 2°), el archivo trae completas la Ley sobre Registro Civil, la de
cambio de nombres y apellidos, la Ley de Menores, la de abandono de familia y
pensiones alimenticias, y la de impuesto a las herencias. Son 9.504 líneas.

Es fiel a BCN, y el nombre del archivo promete menos de lo que contiene.

```bash
grep -nE '^#+ (ARTÍCULO|ARTICULO) [3-8]' ../leychile-texto/codigos/codigo-civil.md
```

---

## B. Robustez del pipeline

### B1. Un fallo transitorio obliga a reconstruir todo — `abierto`

El 2026-08-16, durante una reconstrucción completa, un `git add` falló porque
otro proceso tenía tomado `.git/index.lock`. Un evento de 984.

El `try/except` de `commit_group` asume que un evento fallido se recupera «en la
próxima corrida», pero git sólo añade commits al final: un evento de 2022
reintentado aterriza detrás de 2028. En un repo cuyo valor entero es que
`git log` sea una cronología real, un fallo aislado **no** se recupera por
reintento y obliga a rehacer el repositorio completo.

**Arreglo sugerido.** Envolver la función `git()` en un reintento corto ante
`index.lock` (3 intentos, backoff ~0.5 s). Convierte una reconstrucción completa
en una pausa imperceptible.

Qué tomó el lock quedó sin determinar. Se descartó el sondeo propio
(`git rev-list` ni crea el lock ni falla ante él); los candidatos son los
editores con integración git abiertos sobre el repo.

---

## C. Estado del corpus

### C1. Cifras de referencia — `abierto`

Un cambio de esquema de commits mueve estos números en silencio. La reagrupación
del 2026-08-16, por ejemplo, bajó a la mitad los commits por autor (el máximo
pasó de 200 a 96) y los `[SIN-REGISTRO]` de 401 a 201.

Sirven para detectar desviaciones después de cada reconstrucción. Todos se
obtienen desde `../leychile-texto`.

| cifra | valor al 2026-08-17 | cómo se obtiene |
|---|---|---|
| códigos | 15 | `git ls-files 'codigos/*.md' \| wc -l` |
| documentos | 16 | + la Constitución |
| commits | 988 | `git rev-list --count HEAD` |
| commits legislativos | 984 | `git log --format='%H' --grep='^\[' -E \| wc -l` |
| commits sin norma registrada | 201 | `git log --format='%s' \| grep -c '^\[SIN-REGISTRO\]'` |
| máximo de commits por autor | 96 | `git log --format='%an' \| grep -v 'Norma modificatoria' \| sort \| uniq -c \| sort -rn \| head -1` |
| normas modificatorias | 728 | `git ls-files 'normas/*/*.md' \| wc -l` |
| commits fechados 1970-01-01 | 103 | `git log --format='%aI' \| grep -c '^1970-01-01'` |
| commits de linaje | 3 | `git log --format='%s' \| grep -cE '^\[LINAJE\]'` |

**Arreglo sugerido.** Un script que las compruebe al final de `build_repo` y
avise si divergen.
