# Sugerencias

Ideas sueltas, no evaluadas ni costeadas (para eso está `PENDIENTES.md`). Se
anotan acá para no perderlas, sin compromiso de hacerlas.

---

## Usar más trailers de git además de `Co-authored-by:`

Hoy `signers.py` mete a todo el mundo —autores de moción y firmantes de la
promulgación— como `Co-authored-by:`. Dos trailers estándar de git que calzan
mejor con datos que ya se extraen (o casi):

- **`Signed-off-by:`** para los firmantes de la promulgación (Presidente/Junta
  + ministros), separado de `Co-authored-by:` para los autores de moción. Es
  el trailer que el propio git/DCO usa para "certifico/apruebo esto", que es
  literalmente lo que hace una firma de promulgación. Hoy esa distinción ya
  existe en el código (`resolve_authors` sabe quién escribió vs. quién firmó)
  pero se pierde al mezclar todo en `Co-authored-by:`.
- **`Cc:`** para el Subsecretario que transcribe ("Lo que transcribo a Ud. para
  su conocimiento..."), dato que hoy se descarta directamente
  (`TRANSCRIBER_MARKER_RE` corta el texto ahí). Encaja porque ese rol es
  justamente "notificado, no autor ni firmante".

Ojo: GitHub sólo le da tratamiento visual especial a `Co-authored-by:`
(avatares clicables). `Signed-off-by:` y `Cc:` quedan como texto plano en el
mensaje — sirven para claridad del `git log`/auditoría, no para la UI.

No evaluado: cuánto cambia `build_commit_message`/`resolve_authors`, ni si
conviene que los firmantes salgan de `Co-authored-by:` por completo o se
dupliquen en ambos trailers.
