# Pendientes

Ideas evaluadas y postergadas, con lo que ya se sabe de cada una para no tener
que investigarlo de nuevo.

---

## 1. Historia completa de cada norma modificatoria

**Qué es.** Hoy cada norma que modificó un código o la Constitución se guarda en
`normas/<tipo>/` como **una sola foto**: su texto al entrar en vigencia, que es
prácticamente el original. Pero esas normas también tienen su propia historia:
la Ley 20.050 fue modificada después, igual que cualquier otra.

La idea es darle a cada norma el mismo tratamiento que a los códigos y a la
Constitución: recorrer su línea de tiempo con `get_versiones` y hacer un commit
por cada versión suya, de modo que `git log normas/ley/LEY-20050.md` muestre
cómo cambió *esa ley* en el tiempo.

**Por qué no se hizo todavía: el costo.** Números medidos, no estimados a ojo:

- Hay **714 normas modificatorias distintas** en el corpus actual (16
  documentos). De ellas, 621 son leyes, 52 decretos leyes, 19 autos acordados,
  8 DFL, 5 sentencias y el resto decretos, avisos y rectificaciones.
- Guardarlas como foto única costó **~1 minuto**, porque el 97% ya estaba en
  caché: al buscar los firmantes de cada modificación ya habíamos descargado
  esos textos.
- Darles historia completa es otra cosa. Cada norma necesita al menos una
  llamada a `get_versiones`, más una llamada por cada una de sus versiones.
  Suponiendo un promedio conservador de 5 versiones por norma, son unas
  **4.300 peticiones nuevas**. Con el retardo de 6 segundos que impone el rate
  limit de BCN, eso son **~7 horas** de descarga en el mejor caso, sin contar
  los reintentos por 429.
- El repositorio crecería de forma parecida: hoy son ~980 commits; esto
  agregaría varios miles.

**Cómo implementarlo cuando se decida.** La infraestructura ya está casi toda:

1. `versions.fetch_version_timeline()` ya funciona con cualquier `idNorma`, así
   que sirve tal cual para una norma modificatoria.
2. Habría que convertir cada norma en un `Target` más (con `categoria: "norma"`
   y su ruta en `normas/<tipo>/`), en vez del guardado directo que hace hoy
   `_guardar_norma()`.
3. El resto —orden cronológico global, fechas, autoría, reanudación por
   `state.json`— funciona igual sin tocarlo, porque no depende de qué tipo de
   documento sea.

**Decisión de diseño que habría que tomar antes.** Si las normas entran al orden
cronológico global junto con los códigos, sus commits quedarían intercalados con
los de los códigos. Eso es coherente (todo pasó en la misma línea de tiempo),
pero hace que `git log` del repo completo sea mucho más ruidoso. La alternativa
es construirlas en una rama aparte, o aceptar el ruido y confiar en que la gente
filtre por ruta (`git log -- codigos/`).

**Recomendación.** Hacerlo por etapas: primero un tipo acotado y de alto valor
(por ejemplo sólo las leyes de reforma constitucional), medir cuánto demora de
verdad, y recién ahí decidir si se extiende a las 714.

---

## 2. Cargo real de los autores parlamentarios

Los commits ya traen una sección "Participantes" con el rol de cada persona. Los
firmantes tienen su cargo textual, extraído de la promulgación ("Presidente de
la República", "Ministro de Hacienda"), pero los autores parlamentarios quedan
con un genérico `autor de la moción`.

La razón es que `get_autores_de_la_ley` sólo entrega nombre e id:

    [{"i":"1713","n":"Iván Moreira Barros"}, ...]

No dice si es diputado o senador, ni de qué período. Ese `i` parece ser el
identificador de persona de BCN, así que probablemente se pueda cruzar con las
reseñas parlamentarias que BCN publica en `datos.bcn.cl` para obtener la cámara
y el período. Habría que confirmar que ese endpoint existe y es consultable, y
ver cuántas peticiones extra implica (hay cientos de personas distintas en el
corpus, aunque se repiten mucho, así que la caché ayudaría).

Con eso, en vez de "Iván Moreira Barros — autor de la moción" se podría leer
"Iván Moreira Barros — diputado, autor de la moción".

---

## 3. Linaje para los códigos

La Constitución ya tiene linaje (cuatro cuerpos encadenados en un solo archivo,
ver `discover.CONSTITUCION_LINAJE`). Varios códigos tienen la misma estructura y
hoy empiezan su historia en el decreto que fijó su texto refundido, no en el
cuerpo original:

- El **Código del Trabajo** que seguimos es el texto refundido de 2003 (DFL 1),
  que sucede a la Ley 18.620 de 1987.
- El **Código Civil** está contenido en el DFL 1 del año 2000.

La mecánica de linaje ya está construida y es genérica: sólo hay que investigar,
código por código, cuál es la cadena real de normas antecesoras en BCN —igual
que se hizo para la Constitución— y agregarla a `targets.yaml`. El trabajo es de
investigación, no de programación, y algunos casos van a ser ambiguos.

**Esa investigación está hecha para los casos principales: ver `ISSUES.md` §A.**
Resumen: el Código Civil y el Código del Trabajo anteriores a su texto refundido
no son recuperables (BCN sólo publica el primero desde 2000 y el segundo en
PDF), y el Código de Aguas de 1948 sí lo es, con texto completo bajo la Ley
8.944 (`idNorma=125715`).

---

## 4. Huecos en la cobertura de la Constitución

BCN no tiene texto versionado para todo el período constitucional. Quedan dos
huecos conocidos, que no son fallas del pipeline sino de la fuente:

- **1888 → 1971**: la Constitución de 1925 sólo aparece con versiones desde
  1971 en el DTO 1333.
- **1977 → 1980**: entre el final de la línea de tiempo del DTO 1333 y el inicio
  del DL 3464.

Habría que revisar si BCN tiene esos textos bajo otros `idNorma` que no
encontramos, o si simplemente no están digitalizados con versiones.
