# Referencia SAXO y alcance de esta adaptación

Revisión del 18/11/2026 sobre Chemalog/saxo-stt-d:

| Rama | Commit revisado |
|---|---|
| feature/saxo-atencion-presencial | fe027e9accaaae0862da6448e1aeb8a828f121d8 |
| feat/presencial-canary-nemo-native-timestamps | c08698c2621046e03387a25c01b204c5f3c74573 |
| feat/presencial-jev-v31-contacto | 63cbf230f76d17e6329a1efd908b69effc1faaaf |

Las ramas son acumulativas. V3.1 confirma la ejecución nativa del 16/11,
aunque el documento inicial de NeMo conserva su estado previo. No confundir
la ejecución de Canary V4 con una evaluación real del prompt V3.1.

## Conservado desde el código

- Funciones puras de speech_windows, remove_repetition_loop y
  normalize_native_words: copias con procedencia, sin imports del proyecto SAXO.
- La política extract_four_speaker_timeline, su umbral
  max(1 segundo, duración * 0.01), orden de aparición y rechazo de un quinto canal
  significativo. La ausencia total de canales produce un descarte explícito.
- Timestamps nativos CTC, preservación del texto no alineado y separación entre
  identidad acústica y rol comercial.
- Atribución contextual, anclajes originales y revisión focalizada.

## Qué significa la diarización contextual en estas ramas

El código fuente define una secuencia concreta:

| Función de SAXO | Función en Counter |
|---|---|
| `pilot._split_tokens` / `candidates_preserving_unaligned_text` | Formar intervenciones con palabras, pausas y puntuación, sin depender de etiquetas de hablante previas |
| `classify_adaptive` / `classify_with_provider` | Atribuir cada intervención a ASESOR/CLIENTE con el diálogo completo; Gemini sustituye a JEV |
| `hybrid.fuse_roles` / `correct_role_conflicts` | Cruzar atribución contextual y timeline acústico con anclajes independientes |
| `hybrid_review.targeted_second_pass` | Revisar casos inciertos usando texto y pistas acústicas |

`pilot.py` documenta expresamente que los límites son hipótesis por puntuación,
pausas y longitud. El prompt V3.1 pide no identificar a la persona ni su voz;
`classify_with_provider` devuelve una elección por candidato y conserva sus
palabras, ID y tiempos. JEV no devuelve nuevos límites ni IDs de personas.

Counter copia ahora las funciones puras `_make_candidate` y `_split_tokens`
sin modificar sus cuerpos en `saxo_turn_candidates.py`, y conserva su motivo
de corte y número de palabras. Se aplican por ventana Canary, igual que el
adaptador de SAXO. Se conserva la palabra repetida y se soporta la puntuación
Unicode del original. El texto sin tiempos válidos queda íntegro y revisable.

Gemini realiza esa atribución contextual por intervención; no depende de un
rol fijo por voz. Un mismo rol puede corresponder a varias personas acústicas.
No se añade una división generativa de texto ni una identificación física
por LLM, porque no forman parte del contrato de estas ramas. Los metadatos
registran `attribution_method=text_context_only`, contexto global e identidad
acústica no verificada. La exactitud real sigue pendiente del piloto.

## Entornos

El contexto del ensayo real reporta Python 3.12.3, NeMo 2.7.3,
Torch/Torchaudio 2.8.0 y CUDA 12.8; Canary se ejecutó en
/mnt/saxo-nemo/venv, separado de /opt/saxo/venv.

El contenedor usa Python 3.12 de Ubuntu 24.04 y dos venvs:

| Entorno | Dependencias principales |
|---|---|
| /opt/pipeline | Torch/Torchaudio 2.8.0; Transformers commit 07338b6c74a578868368e6e549dea83414e4b8cb (5.18.0.dev0); Hub 1.33.0; tokenizers 0.23.2; librosa 1.0.0 |
| /opt/canary | NeMo 2.7.3; Torch/Torchaudio 2.8.0; Transformers 4.57.6 |

La versión completa de Transformers/Hub del venv NeMo de AWS no quedó
documentada en el material recuperado. La 4.57.6 se seleccionó dentro del rango
4.57.x exigido por NeMo y se verificó mediante instalación e imports. No se
afirma que sea un freeze idéntico del servidor. Los locks de esta entrega
registran las resoluciones Linux/Python 3.12; no se mezclan ambos grafos y no
se usa --no-deps. No se mantiene el cambio anterior a NeMo 3.0.

Los pesos se identifican por commits inmutables antes de ejecutar. El repo de
modelos es el mismo; la revisión efectivamente cacheada en el ensayo AWS no
se deduce automáticamente del nombre ni de su rama de código.

## Adaptaciones de Counter y Gemini

El workflow mantiene el contrato prepare→worker→roles de Paul en un Job nuevo.
GCS y BigQuery reemplazan las rutas AWS/locales. Las salidas, cuentas y modelos
remotos son propios. Canary se libera antes de la siguiente inferencia Nemotron.

Gemini recibe el diálogo completo y decisiones estructuradas con IDs/evidencias.
No usa el protocolo choice/confidence ni el umbral 0.65 de JEV. Los criterios
de anclaje y aceptación se conservan explícitos como políticas iniciales para
Gemini; aún necesitan calibración sobre nuestros audios. No son probabilidades.

El primer resultado no se considera equivalente en calidad al ensayo de SAXO
por pasar imports o tests. El piloto debe revisar texto, tiempos, speakers,
roles, silencios, respuestas cortas y costo total.
