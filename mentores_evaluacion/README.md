# Pipeline productivo de evaluacion de mentores

Paquete independiente para Cloud Build, siguiendo el patron de Counter/transcripciones.
El build crea la imagen y despliega Cloud Run Job y Workflows. Scheduler esta deshabilitado
por defecto (`_ENABLE_SCHEDULER=false`). La ejecucion manual carga metadatos TRUE/Firestore y consume audios existentes. No copia, descarga
ni transcribe archivos de audio.

## Configuracion reutilizada y verificada

| Parametro | Valor |
|---|---|
| Proyecto | `prd-utpbi-data-operation` |
| Region de computo | `us-central1` |
| Artifact Registry | `serializa-audio-repo` |
| Cuenta Job y Workflow | `genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com` |
| Ubicacion BigQuery | `US` (ambos datasets verificados) |
| Fuente de audios | `raw_cita_promotor.hist_cita_mentor_audio_catalog` |
| Modelo existente | `adf_speech_analytics.gemini-2-5-flash` |
| Conexion existente | `prd-utpbi-data-operation.US.utp_gen_ia_process` |
| Procedimiento almacenado | `raw_cita_promotor.sp_process_speech_analytics_mentores` |
| Firestore | Proyecto `citapromotor-utp`, base `(default)` |
| Secreto de acceso a fuente | `PromotoresSourceSa:latest`, existente en el proyecto |
| Scheduler | Deshabilitado: `_ENABLE_SCHEDULER=false` |
| Horario reservado si se habilita | `0 7 * * *`, `America/Lima` |

La consulta de Scheduler no encontro una programacion de Counter para copiar. Las 07:00 son
un valor inicial configurable con `_SCHEDULE`, no una hora confirmada de transcripciones.
El secreto se monta solo en Cloud Run; su contenido no se incluye en el repositorio ni en la imagen.
Su existencia fue verificada; los permisos de lectura de las colecciones se validan en ejecucion.

## Recursos propios

- Job: `prd-utpbi-mentores-evaluacion`.
- Workflow: `prd-utpbi-mentores-evaluacion-pipeline`.
- Scheduler: `prd-sch-mentores-evaluacion`.
- Imagen: `us-central1-docker.pkg.dev/prd-utpbi-data-operation/serializa-audio-repo/mentores-evaluacion:BUILD_ID`.

## Despliegue mediante Build

Desde esta carpeta:

```bash
gcloud builds submit . \
  --project=prd-utpbi-data-operation \
  --config=cloudbuild.yaml
```

En un activador de un repositorio cuyo root sea `4_prospeccion_mentores`, seleccionar
`05_Despliegue/mentores_pipeline/cloudbuild.yaml` y configurar
`_SOURCE_DIR=05_Despliegue/mentores_pipeline`. Si el repositorio contiene solo este paquete,
usar `_SOURCE_DIR=.`. El repositorio y la rama del activador no se han seleccionado ni creado.

El build primero ejecuta pruebas y construye/publica la imagen. Luego despliega el Job,
ejecuta `init` (sin inferencia), crea/actualiza el SP y despliega el Workflow. Solo crea/actualiza Scheduler si
`_ENABLE_SCHEDULER=true`; si es false y el Scheduler propio ya existe, lo pausa.
Un despliegue nuevo conserva tablas existentes. No modifica recursos de transcripciones.
Con la configuracion por defecto no quedan ejecuciones programadas. El build no dispara evaluaciones de audios.

Se requieren APIs habilitadas de Build, Artifact Registry, Cloud Run, Workflows, Scheduler,
BigQuery, Secret Manager y Firestore. No se conceden permisos IAM automaticamente:

- Cuenta de Build: publicar en el repositorio, desplegar Job/Workflow/Scheduler,
  ejecutar el Job con argumentos distintos y actuar como las cuentas configuradas.
- Cuenta de Job: BigQuery jobUser, escritura en ambos datasets, permisos para crear/actualizar
  y consultar rutinas en `raw_cita_promotor`, acceso al modelo/conexion y
  secretAccessor sobre `PromotoresSourceSa`.
- Identidad del secreto: lectura en las colecciones `mentores`, `dias_utp` y el grupo `audios`.
- Cuenta de Workflow: ejecutar Job con overrides, consultar sus operaciones, BigQuery jobUser
  y lectura de `hist_mentores_execution_plan` para recorrer las fechas.
- Cuenta usada por Scheduler: ejecutar el Workflow; servicio de Scheduler con su rol de agente.
- Cuenta de la conexion BigQuery: lectura sobre los objetos GCS existentes y permisos del modelo.

## Procedimiento almacenado y DDL

El procesamiento queda registrado en BigQuery como:

`prd-utpbi-data-operation.raw_cita_promotor.sp_process_speech_analytics_mentores`

Su definicion completa esta versionada en `sql/02_sp_process.sql`. Durante `init`, el Build
crea las tablas faltantes con `sql/01_init.sql`, prepara catalogos y prompts y registra el SP
mediante `CREATE OR REPLACE PROCEDURE`. Este ultimo paso actualiza codigo, no borra historicos.
Los DDL de tablas son de inicializacion y pueden repetirse porque usan `IF NOT EXISTS`.
No es necesario correrlos manualmente antes de cada procesamiento.

El Job llama al SP con parametros vinculados: `date_from`, `date_to`, `reprocess`, `run_id`,
el token interno `owner` del bloqueo y `snapshot_id`. El SP exige una sola fecha por llamada,
valida el bloqueo y consulta la fotografia de catalogos indicada antes de modificar datos.
Se ejecuta mediante el Workflow con impersonacion, para mantener la carga previa de catalogos,
auditoria y control de concurrencia. Un `CALL` aislado sin bloqueo es rechazado.
No se incluyen ejecuciones de prueba con fechas fijas dentro del archivo de despliegue del SP.

## Ejecucion y reproceso

Las ejecuciones manuales se inician con impersonacion explicita de
`genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com`.
El usuario que inicia el comando requiere `roles/iam.serviceAccountTokenCreator` sobre esa
cuenta, y la cuenta impersonada requiere permiso para ejecutar el Workflow
(`roles/workflows.invoker`). Esto no cambia la cuenta del activador de Build.

Desde esta carpeta tambien puede usarse `python scripts/run_manual.py`, que incluye
siempre la impersonacion. Para reproceso: `python scripts/run_manual.py --date-from 2026-09-01 --date-to 2026-09-30 --reprocess`.

Ejecucion habitual desde Cloud Shell:

```bash
gcloud workflows execute prd-utpbi-mentores-evaluacion-pipeline \
  --project=prd-utpbi-data-operation --location=us-central1 \
  --impersonate-service-account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com \
  --data='{}'
```

Reproceso forzado de un rango inclusivo:

```bash
gcloud workflows execute prd-utpbi-mentores-evaluacion-pipeline \
  --project=prd-utpbi-data-operation --location=us-central1 \
  --impersonate-service-account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com \
  --data='{"date_from":"2026-09-01","date_to":"2026-09-30","reprocess":true}'
```

El Workflow primero ejecuta el Job en modo `prepare`: carga los tres catalogos una sola vez
y guarda un plan de fechas en BigQuery. Luego recorre ese plan en orden ascendente con un
bucle secuencial: por cada fecha inicia una ejecucion independiente del Job, espera su
finalizacion y solo entonces inicia la siguiente. Cada llamada al SP procesa un solo dia.
Todos los dias del rango usan la misma fotografia de catalogos, aunque otro flujo publique
una fotografia mas reciente. El timeout del Job se aplica por fecha, no al rango completo.

Con fechas explicitas, el plan contiene todos los dias del rango inclusivo. `reprocess=true`
fuerza una nueva evaluacion; con false se reutilizan los resultados vigentes. Con `{}`, busca
fechas hasta ayer en Lima con audios nuevos, pendientes, errores o cambios de hash del prompt;
incluye los ingresos tardios. Si no hay fechas candidatas, termina con cero dias procesados.
El primer recorrido puede evaluar todo el pendiente historico, separado por dia.
`process_date` corresponde al dia que se esta procesando; `fecha_audio` conserva la fecha
original del audio.

Se conserva la regla existente: `copy_result='copied'`, URI no nula y duracion de al menos
60 segundos. Los casos sin cruce o tipo de charla valido quedan pendientes. Se usan los
prompts Markdown vigentes de laboratorio y auditorio; el despliegue registra su texto y hash.

## Historicos y publicacion

Todos los siguientes objetos estan en `prd-utpbi-data-operation.raw_cita_promotor`:

| Objeto | Contenido |
|---|---|
| `hist_maestro_mentores_firestore` | Fotografia de mentores por snapshot |
| `hist_maestro_audios_mentores_firestore` | Fotografia de metadatos de audio por snapshot |
| `hist_dias_utp` | Fotografia de eventos por snapshot |
| `hist_mentores_catalog_snapshots` | Fotografias publicadas completas |
| `maestro_mentores_firestore`, `maestro_audios_mentores_firestore`, `dias_utp` | Vistas de la ultima fotografia publicada |
| `hist_mentores_audio_control` | Estado actual por URI |
| `hist_mentores_control_ejecucion` | Copia del control por ejecucion |
| `hist_mentores_evaluaciones` | Todas las versiones validas de evaluaciones |
| `hist_mentores_ia_respuestas` | Respuestas IA validas y fallidas por ejecucion |
| `sys_prompts`, `hist_mentores_prompt_versions` | Prompt vigente y versiones del texto |
| `hist_mentores_pipeline_runs` | Fechas, estado y error de cada ejecucion |
| `hist_mentores_execution_plan` | Fechas ordenadas y fotografia de catalogos por Workflow |
| `mentores_pipeline_lock` | Exclusividad de la ejecucion |

Destino final:
`prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd`.
Conserva el ultimo resultado valido por URI. Sus versiones anteriores permanecen en
`raw_cita_promotor.hist_mentores_evaluaciones`.

Las tres fotografias y su marca de publicacion se insertan en una transaccion. Una falla
conserva la fotografia publicada anterior. Los resultados, sus versiones y el estado final de
control tambien se publican en una transaccion. Nunca se reemplaza toda una tabla historica.
Un reproceso usa los catalogos vigentes al ejecutarse; no reconstruye el estado pasado de TRUE.
El SP lee directamente las tablas historicas filtradas por el `snapshot_id` del plan; las vistas
de ultima fotografia siguen disponibles para consultas externas.

## Concurrencia, errores y limites

El Job tiene una tarea, sin reintentos automaticos. Un bloqueo transaccional impide dos
evaluaciones simultaneas; una segunda ejecucion falla con `PIPELINE_OCUPADO`.
Las tablas de trabajo del SP son temporales y estan aisladas por sesion. La tabla externa
de objetos es compartida, protegida por el bloqueo y con vencimiento de dos dias. El bloqueo
vence a las ocho horas si Cloud Run termina abruptamente (timeout del Job: siete horas).
Si sucede, comprobar que no haya jobs BigQuery activos antes de intervenir el bloqueo.

Si un dia falla tecnicamente, el Workflow se detiene y no inicia las fechas siguientes.
Los dias que ya terminaron conservan sus resultados. Para continuar, iniciar otro Workflow
desde la fecha fallida hasta el final del rango. Un reproceso forzado del rango original
volveria a evaluar tambien las fechas ya completadas.
Los errores individuales de IA quedan registrados y pendientes de revision/reintento; el
bucle puede continuar cuando el Job termina y publica correctamente su estado de control.
Si un solo dia tiene un volumen excesivo, sera necesario dividir ese dia en lotes adicionales;
esta version limita por fecha, no por cantidad de audios.

El identificador del job BigQuery de evaluacion es estable por ejecucion del Workflow y fecha para
evitar repetir inferencia por una respuesta de red perdida. Para reintentar una ejecucion
que fallo, iniciar una nueva ejecucion del Workflow. Los resultados previos validos se reutilizan
en modo normal; usar `reprocess=true` para forzar una nueva evaluacion del rango.
Una ejecucion puede completar con audios pendientes o con errores de IA; revisar las tablas
de control y respuestas. `SUCCESS` significa que el pipeline completo su publicacion.

Las tablas ya existentes deben tener el esquema de este paquete: `init` no migra esquemas
ni importa datos desde `raw_genesys_audios`. Si un nombre de vista ya pertenece a una tabla,
BigQuery rechaza el cambio en vez de eliminar la tabla existente.

## Desarrollo y validacion local

Los SQL productivos y los normalizadores se generan desde las carpetas originales:

```bash
python scripts/prepare_sql.py
python -m unittest discover -s tests -v
```

La regeneracion requiere el arbol original de carpetas. El paquete entregado ya contiene
los SQL, prompts y normalizadores generados; Cloud Build no necesita acceder a las carpetas originales.
Las pruebas no ejecutan cargas ni inferencia. Una prueba completa de permisos y ejecucion
requiere desplegar en Google Cloud y ejecutar un rango controlado.
El cambio a SP se valido inicialmente con 13 pruebas locales y un dry run de su definicion en BigQuery,
usando impersonacion de `genesys-audio-processor` (sin crear la rutina ni evaluar audios).
La definicion usa `strict_mode=FALSE` por sus tablas temporales y el objeto externo dinamico;
el dry run no sustituye una ejecucion controlada despues del despliegue.
La version con bucle secuencial por fecha paso 19 pruebas locales y un nuevo dry run del SP
con impersonacion. El Workflow se valido estructuralmente; aun no se ha desplegado ni
ejecutado el rango completo en Google Cloud.

Referencias de implementacion: [variables Cloud Build](https://docs.cloud.google.com/build/docs/configuring-builds/substitute-variable-values),
[overrides de Cloud Run](https://docs.cloud.google.com/run/docs/execute/jobs),
[Workflows con Cloud Run Jobs](https://docs.cloud.google.com/workflows/docs/tutorials/execute-cloud-run-jobs).
