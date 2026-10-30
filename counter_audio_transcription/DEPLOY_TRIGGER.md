> Prueba de dos audios sin Secret Manager: ver [PRUEBA_DIARIZACION_MANUAL.md](PRUEBA_DIARIZACION_MANUAL.md).

> v0.3.0: CPU optimization pilot. See [PILOTO_CPU_OPTIMIZADO.md](PILOTO_CPU_OPTIMIZADO.md) for the current batch behavior and isolated deployment settings. FILES_PER_WORKER controls files per task; each audio keeps independent outputs. Whisper is bundled and reused. No production deployment was executed by this change.

# Counter: despliegue desde Git mediante Cloud Build

El despliegue se inicia al hacer commit y push a la rama configurada en el activador conectado al repositorio Git:

`Git (push) → activador Cloud Build → validación y pruebas → imagen Docker → Artifact Registry → Cloud Run Job + Workflow`.

Configurar el repositorio, la rama y las sustituciones de la tabla siguiente en el activador. Los valores del activador reemplazan los valores por defecto de cloudbuild.yaml. Si el contexto es el repositorio padre, usar el filtro de archivos incluidos `counter_audio_transcription/**`; si esta carpeta es la raíz del repositorio, usar `**`.

La configuración del activador en GCP debe existir previamente; estos archivos no crean ni conectan el activador. Tras el despliegue exitoso, ejecutar el Workflow para iniciar el lote de audios.

El activador usa `counter_audio_transcription/cloudbuild.yaml` y `_SOURCE_DIR=counter_audio_transcription` si el contexto es el repositorio padre. Si la carpeta es la raíz del repositorio, usar `cloudbuild.yaml` y `_SOURCE_DIR=.`.

Cloud Build valida parámetros, construye y publica la imagen, despliega el Cloud Run Job y despliega Workflows. No ejecuta audios, crea tablas, asigna IAM ni programa Scheduler. La función HTTP individual continúa siendo opcional y no se despliega desde este flujo.

## Piloto de solo transcripción (versión 0.2.0)

Agregar al activador `_ENABLE_DIARIZATION=false`, `_BATCH_MAX_FILES=1` y `_PARALLELISM=1`. Luego hacer commit y push de esta entrega. El build despliega; ejecutar el Workflow con `{}` inicia el procesamiento de un archivo pendiente.

Con diarización desactivada no se exige que exista counter-hf-token ni se descarga/carga el modelo Pyannote. El despliegue retira únicamente la referencia HF_TOKEN del Job Counter si estaba configurada, sin borrar el secreto en Secret Manager. Puedes conservar _HF_SECRET y _HF_SECRET_VERSION en el activador: no se utilizan en este modo. La imagen sigue incluyendo las dependencias de ambos modos para permitir activación posterior.

Las salidas conservan texto y tiempos, speaker_id=null, diarization=null y diarization_status=disabled. No se infieren hablantes, roles ni solapamientos. El TXT presenta tiempos y texto sin etiquetas de hablante. El cambio de modo forma parte de la clave de procesamiento, por lo que activar diarización después produce resultados separados.

Para activar diarización posteriormente: `_ENABLE_DIARIZATION=true`, completar secreto/versión existentes y permisos, y volver a desplegar desde Git.

## Sustituciones

| Parámetro | Valor / propósito |
| --- | --- |
| `_PROJECT_ID` | Proyecto destino; por ejemplo `prd-utpbi-data-operation` |
| `_LOCATION` | Región del Job y Workflow, por confirmar |
| `_REPOSITORY` | Repositorio Docker existente en Artifact Registry |
| `_SERVICE_ACCOUNT` | Cuenta de servicio del Job |
| `_WORKFLOW_SERVICE_ACCOUNT` | Cuenta de servicio del Workflow |
| `_ALLOWED_INPUT_BUCKETS` | `prd-utp-stg-queuesmart` para el catálogo indicado |
| `_OUTPUT_GCS_ROOT` | Por ejemplo `gs://prd-utp-stg-queuesmart/counter/results` |
| `_MANIFEST_GCS_ROOT` | Por ejemplo `gs://prd-utp-stg-queuesmart/counter/manifests`; prefijo dedicado, sin barra final |
| `_BQ_SOURCE_TABLE` | Ya configurado: `prd-utpbi-data-operation.raw_queue_smart.queuesmart_mp3_catalog` |
| `_BQ_SOURCE_LOCATION` | Ubicación real del dataset origen; confirmar, no inferir de la región de Cloud Run |
| `_BQ_RESULTS_TABLE` | Tabla de resultados existente; vacío deshabilita esta salida |
| `_BQ_LOCATION` | Ubicación del dataset de resultados; obligatoria si se habilita la tabla |
| `_BATCH_MAX_FILES` | Máximo de pendientes por ejecución; inicial `100`, piloto recomendado `3` |
| `_PROCESS_DAY_FROM`, `_PROCESS_DAY_TO` | Fechas inclusivas YYYY-MM-DD; vacías revisan todo el catálogo |
| `_PARALLELISM` | Máximo de tareas concurrentes por ejecución; inicial `2` |
| `_JOB_NAME`, `_WORKFLOW_NAME` | Nombres configurables de los recursos |
| `_ENABLE_DIARIZATION` | `false` para solo transcribir; `true` para transcribir y diarizar |
| `_HF_SECRET`, `_HF_SECRET_VERSION` | Solo requeridos cuando _ENABLE_DIARIZATION=true; no poner el token en Git |
| `_CPU`, `_MEMORY`, `_TASK_TIMEOUT` | Iniciales: 4, 16Gi, 7200s por tarea |

Los modelos y sus umbrales permanecen configurables en `config/job.env.example.yaml`; no hace falta duplicarlos en las sustituciones. No se leen archivos `*.local.yaml` en Cloud Build.

## Ejecución por lotes

Ejecutar el Workflow desplegado con entrada `{}`. Genera un manifiesto único, prepara como máximo N pendientes y ejecuta una tarea por archivo. Un lote vacío termina sin workers. Cada ejecución procesa un lote; repetir hasta agotar pendientes o configurar posteriormente Scheduler para llamar al Workflow. No lanzar lotes solapados: pueden seleccionar los mismos pendientes; los locks protegen cada archivo pero una tarea duplicada puede terminar con error.

La selección deduplica por `gcs_uri`, conservando la fila con `load_date` más reciente. Por defecto no filtra por ayer: contempla incorporaciones tardías. El límite se aplica después de omitir resultados completados, evitando que los primeros archivos bloqueen permanentemente los siguientes. Se verifica la generación GCS y la configuración entre prepare y worker.

Se consulta el catálogo y se revisa el estado GCS de los candidatos hasta reunir el lote: esta primera versión prioriza corrección, pero ese recorrido puede encarecerse al crecer el histórico. Una fila con objeto inaccesible o inválido hace fallar prepare y debe corregirse antes de reintentar; no se omite silenciosamente.

Cada segmento conserva `catalog` en result.json y en `serialized_level_2` de BigQuery: metadatos de origen, original_recording_id derivado de s3_uri (fallback gcs_uri), segment_index y alcances de hablantes/tiempos. No se concatenan audios ni se calculan tiempos globales; no se asume continuidad de hablantes o una atención por archivo. `recording_id` identifica el archivo GCS, no toda la grabación original.

Para resincronizar resultados ya completados con BigQuery o actualizar metadatos, ejecutar prepare con `INCLUDE_COMPLETED=true` y luego workers desde ese manifiesto. Esto reutiliza inferencia existente. El filtro normal los omite. En un reproceso individual usar el mismo recording_id calculado por URI y FORCE=true. Si se cambian modelos, cambia la clave; si se cambia la implementación/dependencias, actualizar PIPELINE_VERSION.

## Permisos y recursos previos

- Job: lectura de catálogo (BigQuery Data Viewer) y ejecución de consultas (BigQuery Job User), lectura de audios, acceso al secreto y lectura/escritura de estados/manifiestos/resultados GCS. Si se habilita BigQuery, escritura en su tabla existente.
- Workflow: ejecutar el Job con overrides, consultar operaciones/ejecuciones y leer el prefijo de manifiestos. Referencia: https://docs.cloud.google.com/workflows/docs/reference/googleapis/run/v2/projects.locations.jobs/run
- Cloud Build: publicar imagen, desplegar Job y Workflows y actuar como ambas cuentas de servicio.
- Habilitar las APIs correspondientes, disponer de repositorio, buckets, cuentas y secreto; aceptar condiciones del modelo en Hugging Face.

Para el piloto: BATCH_MAX_FILES=3, escoger opcionalmente un rango de process_day y revisar resultados, tiempos y memoria. Luego ampliar volumen y programar el Workflow. Build, IAM, inferencia y calidad real todavía requieren validación en GCP.
