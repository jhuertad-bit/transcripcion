# Transcripción y diarización de prospección

Implementación independiente, adaptada del worker CPU de Counter. Procesa los audios de promotores y mentores sin concatenarlos ni modificar los catálogos. Incluye los DDL acordados, el worker, el control de intentos, el contenedor, Cloud Build y el Workflow.

Estado de entrega: implementación local y pruebas con dependencias simuladas. No se ha desplegado ni ejecutado inferencia en GCP desde esta entrega. El usuario confirmó la creación del bucket, dataset y tres tablas; no se verificó su estado remoto.

## Orígenes y destinos

Proyecto: `prd-utpbi-data-operation`. BigQuery: `US`.

| tipo_origen | Catálogo | Bucket de lectura |
|---|---|---|
| promotor | `raw_cita_promotor.hist_cita_promotor_audio_catalog` | `prd-utp-stg-citapromotor-9bd1430535b8` |
| mentor | `raw_cita_promotor.hist_cita_mentor_audio_catalog` | `prd-utp-stg-mentores` |

Se procesa `gcs_uri`, la copia existente del audio. `source_gcs_uri` se conserva como trazabilidad; no se descarga desde Firebase. `folder_uuid` se conserva como STRING, sin atribuirle un significado de negocio no confirmado.

La selección utiliza `fecha_audio`, deduplica cada URI por `fecha_procesamiento DESC` y admite los estados de copia `copied` y `already_in_gcs`, tomados del copiador existente. No utiliza los campos de Counter `process_day` ni `load_date`. No hay JOIN entre catálogos: se procesan independientemente.

Tablas de salida:

- `raw_transcripciones.hist_prospeccion_audio_transcripciones`: resultados publicados, una fila por `processing_key`.
- `raw_transcripciones.hist_prospeccion_audio_intentos`: una fila por `attempt_id`, actualizada durante el intento.
- `raw_transcripciones.hist_prospeccion_audio_roles`: estructura reservada para clasificación semántica posterior. Este worker NO la llena.

GCS:

```text
gs://prd-utp-stg-transcripciones/transcripciones/prospeccion/
  manifests/<promotor|mentor>/<batch_id>.json
  manifests/<promotor|mentor>/<batch_id>.json.meta.json
  <promotor|mentor>/<processing_key>/
    processing.lock
    status.json
    completed.json
    attempts/<attempt_id>/result.json
    attempts/<attempt_id>/transcript.txt
```

No se crean carpetas vacías. La escritura de objetos genera los prefijos.

## Configuración operativa

El activador permite cambiar la diarización y los ajustes del lote. Los valores iniciales están en `VARIABLES_ACTIVADOR.md`. BigQuery, GCS y los parámetros avanzados se configuran en `config/job.env.example.yaml`.

Para cambiar diarización usa `_ENABLE_DIARIZATION=true|false` y ejecuta otro build. Las fechas y los orígenes se pasan al Workflow.

## Archivos

```text
sql/00_create_dataset.sql          Dataset US, opcional si ya existe
sql/01_create_transcripciones.sql  DDL exacto de transcripciones
sql/02_create_intentos.sql         DDL exacto de control
sql/03_create_roles.sql            DDL exacto de roles, etapa futura
sql/04_consultas_validacion.sql    Texto, segmentos y estados
config/job.env.example.yaml       Configuración sin credenciales
worker/catalog.py                 Selección y manifiestos
worker/main.py                    Preparación y ejecución por lotes
worker/pipeline.py                Transcripción, diarización y publicación
worker/bigquery_sink.py           MERGE de resultados e intentos
worker/storage.py                 Generaciones, locks y archivos GCS
worker/models.py                  Faster Whisper y Pyannote
worker/alignment.py               Asociación temporal palabra/hablante
worker/audio.py                   Normalización FFmpeg
worker/Dockerfile                 Imagen CPU con Whisper incluido
prospeccion_common/contract.py    Validación de URI y solicitud
workflows/daily_pipeline.yaml     Orquestación por origen
cloudbuild.yaml                   Pruebas, imagen y despliegue
scripts/cloudbuild_deploy.sh      Despliegue del Job y Workflow
DEPLOY.md                         Preparación, permisos y ejecución
VERIFICACION.md                   Evidencia de pruebas y límites
tests/                            Pruebas sin nube ni modelos
```

Los DDL 01–03 reproducen los campos, tipos, particiones y clustering creados durante la conversación. `IF NOT EXISTS` no corrige esquemas diferentes; comprobarlos antes del primer lote.

## Procesamiento

1. El Workflow recibe fechas explícitas y una lista de orígenes; por defecto ejecuta promotor y después mentor.
2. `prepare` consulta el catálogo y captura la generación de cada objeto GCS. Omite resultados con `completed.json` antes de aplicar el límite de lote.
3. Publica un manifiesto inmutable con los audios pendientes y la configuración de modelos.
4. Cada tarea procesa hasta `FILES_PER_WORKER` audios de forma secuencial. Los modelos Whisper se reutilizan dentro del proceso.
5. Antes de descargar, el worker registra un intento en BigQuery; valida que la generación no haya cambiado y obtiene un lock por audio/configuración.
6. FFmpeg convierte a mono, PCM de 16 kHz. Faster Whisper transcribe con tiempos de palabras; Pyannote Community-1 diariza. La asociación temporal conserva segmentos ambiguos o sin hablante identificado.
7. Escribe JSON y TXT por intento, hace MERGE de la transcripción y publica `completed.json` después del éxito. Finalmente cierra el intento en BigQuery.

`serialized_level_1` contiene el texto con tiempos y speaker_id. `serialized_level_2` contiene el JSON completo: segmentos/palabras, diarización regular y exclusiva, metadatos del catálogo, modelos, paquetes y tiempos. `result_uri` enlaza al mismo resultado en GCS.

`recording_id` = primeros 24 caracteres SHA256 de `prospeccion|tipo_origen|gcs_uri`. `processing_key` = SHA256 de identidad, fuente (URI, generación y tamaño) y configuración de modelos. Cada reintento tiene un UUID distinto; su identificador coincide con el del resultado GCS cuando hay inferencia nueva.

La fecha de carga del catálogo se guarda en `fecha_procesamiento_catalogo`. `processed_at` corresponde al resultado de inferencia. Los TIMESTAMP están en UTC; para mostrarlos en Perú usar `DATETIME(processed_at, 'America/Lima')`.

## Estados y reintentos

Estados de intentos: `queued`, `processing`, `completed`, `failed`. Etapas: `descarga`, `transcripcion`, `diarizacion`, `publicacion`. `queued` se escribe al comenzar el worker, no al seleccionar el catálogo; no representa la cola completa de Cloud Run.

- Un fallo de un archivo no impide que los siguientes de su tarea se procesen. Al final la tarea falla si hubo errores.
- Si falla el Job de un origen, el Workflow termina con error y el siguiente origen no se ejecuta; se puede lanzar solo ese origen o el pendiente con `tipos_origen`.
- Al repetir el rango, se seleccionan pendientes. Un resultado publicado se omite por defecto.
- Para resincronizar resultados GCS existentes con BigQuery, configurar `INCLUDE_COMPLETED=true` en el Job. Con `FORCE=false` se reutilizan sin inferencia. Volver a `false` después.
- Para inferir de nuevo la misma configuración, usar `INCLUDE_COMPLETED=true` y `FORCE=true`. Restaurar ambos al finalizar. Una versión de modelo/pipeline distinta genera otra clave y conserva el resultado anterior.
- Si BigQuery de resultados falla antes de publicar, no se cambia `completed.json`. Si falla el registro final del intento después de publicar, el Job puede fallar aunque la transcripción ya esté disponible: revisar resultados, GCS y logs.
- Si el contenedor termina abruptamente, un intento puede quedar `processing` y un lock permanecer. Confirmar que no existe ejecución activa antes de retirar manualmente exclusivamente ese lock. No hay caducidad ni recuperación automática de locks.
- Fallos durante `prepare` (por ejemplo URI inexistente al capturar su generación) aparecen en logs del Job, sin fila de intento. La selección se detiene; corregir el catálogo/objeto antes de repetir.
- No ejecutar lotes superpuestos en paralelo: los locks evitan doble inferencia, pero un competidor terminará con error. BigQuery no impone unicidad sobre las claves; MERGE y la ejecución controlada mantienen la idempotencia.

El piloto limita a 3 archivos POR ORIGEN, 3 archivos por tarea y paralelismo 1. Para completar días grandes, repetir el mismo rango hasta obtener cero pendientes, o ajustar el límite según mediciones. Una ejecución no drena automáticamente todo el catálogo.

## Modelos y alcance

Escenario confirmado: un promotor o mentor brinda un speech a un grupo de estudiantes de colegio, con preguntas o intervenciones ocasionales. INITIAL_PROMPT incorpora este contexto para Whisper; no constituye una instrucción de clasificación ni garantiza el contenido reconocido. No es una conversación comercial de dos personas.

Para la futura clasificación, distinguir rol expositor (promotor o mentor según tipo_origen), estudiante y desconocido. Varios speaker_id pueden tener rol estudiante. No identificar al expositor únicamente por ser SPEAKER_00, hablar primero o acumular más tiempo. Las voces lejanas, breves, corales o simultáneas pueden quedar sin asignación fiable; no se identifica nominalmente a estudiantes.

MAX_SPEAKERS=6 es el tope inicial de voces diferenciadas por audio, no la cantidad de asistentes. Revisarlo escuchando las muestras antes del lote completo si intervienen más de seis personas. No se fuerza un mínimo de dos: puede haber audios donde solo habla el expositor.

Diarización habilitada por defecto; CPU de 4 hilos, Whisper `deepdml/faster-whisper-large-v3-turbo-ct2`, Pyannote `pyannote/speaker-diarization-community-1`. Límite: 500 MiB por audio y 7200 segundos. Si el audio supera la duración, falla en lugar de publicar una transcripción truncada.

Se necesita aceptar las condiciones del modelo Pyannote y un token Hugging Face con acceso, configurado como HF_TOKEN manual en el Job (modo actual de Counter) o mediante Secret Manager. No incorporar tokens a Git ni a la imagen. Ver [modelo oficial](https://huggingface.co/pyannote/speaker-diarization-community-1).

Los speaker_id solo valen dentro de cada audio y no identifican personas. No se supone que SPEAKER_00 sea promotor/mentor. El rol queda nulo; no se llama a Gemini en esta versión. Los solapamientos y las palabras ambiguas requieren revisión; la diarización no separa las fuentes de audio.

Las dependencias y pesos mantienen la base de Counter; algunos paquetes están definidos con rangos y los pesos no se fijan por commit. Tras el primer build/piloto validado, congelar versiones y revisiones para reproducibilidad, e incrementar `PIPELINE_VERSION` cuando cambie el procesamiento.
