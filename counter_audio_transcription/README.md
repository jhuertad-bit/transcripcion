# counter_audio_transcription: transcripción y diarización de audios Counter

Código y plantillas locales preparados el 20/10/2026. **No se han creado ni modificado objetos en GCP, ejecutado builds, descargado modelos o procesado audios reales.** La ejecución e infraestructura quedan a cargo del usuario. Genesys/Markov permanece independiente.

## Qué contiene

| Componente | Archivo / carpeta | Responsabilidad |
| --- | --- | --- |
| Cloud Function HTTP | `main.py`, `cf_trigger/`, `requirements.txt` | Validar una solicitud y llamar a la API Jobs de Cloud Run |
| Contrato Counter | `counter_common/` | Validar URI, bucket, identificador y parámetros admitidos |
| Cloud Run Job | `worker/` | Descargar, normalizar, transcribir, diarizar y guardar resultados |
| Imagen CPU inicial | `worker/Dockerfile` | Entorno Linux con FFmpeg, faster-whisper y pyannote |
| Cloud Build | `cloudbuild.yaml`, `scripts/` | Validación, build, push y despliegue del Job |
| Configuración sin secretos | `config/*.example.yaml` | Nombres y parámetros por completar |
| Plantilla SQL manual | `sql/01_create_results_table.sql` | Crear tabla de resultados si se habilita BigQuery |
| Solicitud de ejemplo | `samples/request.example.json` | Una URI por ejecución |
| Pruebas sin nube/modelos | `tests/test_counter.py` | Contratos, asignación temporal y control del procesamiento |

Flujo: URI existente de GCS → función HTTP (opcional) → una ejecución del job → descarga → WAV mono 16 kHz → faster-whisper → pyannote Community-1 → palabras por hablante → JSON/texto en GCS → tabla de resultados opcional en BigQuery.

Se puede ejecutar el job directamente antes de desplegar la función. Ningún script de este proyecto despliega por el simple hecho de abrirlo o ejecutar las pruebas.

## Decisiones del primer prototipo

- Una URI y una tarea por ejecución. Se rechaza ejecutar el worker con más de una tarea; no hay repartición de lotes por índice.
- Inferencia CPU secuencial para reducir concurrencia de modelos en memoria. Es una base para medir, no un dimensionamiento certificado.
- Se conservan las pausas y tiempos del audio. Downmix a mono no separa voces.
- Faster-whisper produce palabras con tiempos. Pyannote produce diarización regular y exclusiva. Se usa la exclusiva para asignar palabras y la regular para marcar solapamientos.
- La asignación usa intersección temporal, umbral mínimo y margen respecto del segundo candidato. Ante insuficiencia o ambigüedad, `speaker_id` es null. El score temporal no es una probabilidad acústica.
- El límite inicial de hablantes es 1–6, configurable; no obliga a dos interlocutores. Los roles asesor/cliente no se infieren.
- Palabras sin intervalo válido y segmentos sin timestamps se preservan en `timestamp_gaps`; no se consideran correctamente alineados.
- No se detectan límites entre varias atenciones dentro del mismo archivo. No se separan fuentes superpuestas ni se garantiza recuperar todas sus palabras.

## Recursos que crearás o completarás en tu cuenta

Los nombres siguientes son propuestos. El ID de proyecto de Markov es `prd-utpbi-data-operation`; confirma si Counter usará ese mismo proyecto.

| Recurso | Propuesta / dato requerido |
| --- | --- |
| Proyecto GCP | Uno existente; no se requiere crear proyecto adicional |
| Bucket de entrada | El de Counter que ya existe; registrar su nombre en `ALLOWED_INPUT_BUCKETS` |
| Salidas | Prefijo propio `counter/results` en un bucket autorizado existente; no necesita bucket nuevo |
| Artifact Registry | Repositorio Docker existente o uno que crees, por ejemplo `counter-containers` |
| Imagen | `REGION-docker.pkg.dev/PROJECT/REPOSITORY/counter:0.1.0` |
| Job | `prd-utpbi-counter-audio-transcripcion` |
| Función HTTP gen2 | `prd-utpbi-counter-cf-transcripcion`, entry point `trigger_counter` |
| Identidades | Cuenta de servicio del job y otra de la función, o identidades existentes con permisos adecuados |
| Secreto | `counter-hf-token`, con un token de lectura autorizado para el modelo |
| Dataset opcional | `raw_counter_audios`, ubicación por confirmar |
| Tabla opcional | `counter_audios_transcripcion`; SQL incluido |

No crear los objetos Genesys ni apuntar Counter a sus tablas o prefijos. Confirmar ubicación del bucket, dataset y cómputo antes de elegir la región. `us-central1` es la configuración de Markov, no una ubicación ya validada para Counter.

## Permisos para quien ejecuta cada componente

| Identidad | Acceso necesario |
| --- | --- |
| Cuenta del job | Lectura de objetos del bucket Counter; creación, lectura, actualización y eliminación de objetos en el prefijo de resultados; lectura del secreto HF |
| Cuenta del job, si habilitas BigQuery | Ejecutar jobs de BigQuery en el proyecto y leer/actualizar la tabla de resultados existente |
| Cuenta de la función | Ejecutar el job **con overrides**: `roles/run.jobsExecutorWithOverrides` sobre el job, o rol personalizado equivalente |
| Usuario/sistema que llama la función gen2 | Invocación autenticada del servicio subyacente (`roles/run.invoker`) |
| Usuario que crea/build/despliega | Permisos de build, repositorio, despliegue y uso de las cuentas de servicio; según política de tu cuenta |

Para el piloto puede usarse Object Viewer en entrada y Object User en el bucket de salida; restringir al alcance adecuado según la política existente. La función no necesita leer audios ni el token de Hugging Face.

En ejecución GCP se usa la identidad de servicio con ADC. Para una prueba desde tu equipo, puedes apuntar `GOOGLE_APPLICATION_CREDENTIALS` a la llave existente. No copiar la llave al código, imagen o variables YAML. `HF_TOKEN` se inyecta como secreto, nunca dentro del Dockerfile.

## Antes de la primera ejecución

1. Aceptar personalmente las condiciones del modelo `pyannote/speaker-diarization-community-1` en Hugging Face y preparar un token de lectura. No está realizado por este código.
2. Seleccionar una URI real y comprobar si contiene una sola atención. La lista de 10–15 audios es para ampliar la evaluación, no un requisito para el primer archivo.
3. Completar bucket de entrada, prefijo de salida y región en copias de las plantillas. Usar archivos `*.local.yaml`, excluidos del envío/build.
4. Confirmar permisos y acceso de red a los repositorios de modelos. Los modelos se descargan en runtime en este prototipo; no se han incluido sus pesos en la imagen. El arranque inicial consume tiempo y espacio temporal.
5. Elegir primero GCS como destino suficiente para el piloto. Habilitar BigQuery después de crear dataset/tabla y confirmar su ubicación.

## Pruebas locales sin GCP ni modelos

Desde la raíz `counter_audio_transcription`, con Python 3.11+:

```bash
python -m unittest discover -s tests -v
```

Las pruebas usan dobles en memoria: no necesitan llave, token, Docker, modelos ni paquetes de GCP. Verifican casos de empate, falta de voz, solapamiento real, claves por versión/generación, idempotencia y conservación del resultado previo si falla un reproceso. No certifican la calidad de reconocimiento ni el funcionamiento de un despliegue.

Para probar la función HTTP localmente, instalar solamente `requirements.txt` y ejecutar:

```bash
functions-framework --target=trigger_counter --port=8080
```

La función requiere las variables de `function.env.example.yaml` y credenciales válidas para solicitar el job. Iniciar el framework por sí solo no crea objetos; **enviar un POST válido sí solicitará una ejecución en tu cuenta**.

## Build y despliegue MANUALES

Los comandos siguientes están para que tú los ejecutes, después de crear los recursos requeridos. No han sido ejecutados por el asistente. Están escritos para **Bash / Cloud Shell**, desde `counter_audio_transcription`; no usar las continuaciones `\` en PowerShell.

Completar variables y copiar las plantillas a `config/job.local.yaml` y `config/function.local.yaml`. Los YAML deben tener los mismos valores que las variables correspondientes.

```bash
PROJECT_ID="YOUR_PROJECT_ID"
REGION="YOUR_REGION"
REPOSITORY="YOUR_EXISTING_DOCKER_REPOSITORY"
JOB_NAME="prd-utpbi-counter-audio-transcripcion"
FUNCTION_NAME="prd-utpbi-counter-cf-transcripcion"
JOB_SA="YOUR_JOB_SERVICE_ACCOUNT_EMAIL"
FUNCTION_SA="YOUR_FUNCTION_SERVICE_ACCOUNT_EMAIL"
HF_SECRET="counter-hf-token"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/counter:0.1.0"
```

El flujo automatizado construye la imagen, la publica en Artifact Registry y crea o actualiza el Job. Usa un tag único por BUILD_ID. Desde esta carpeta, en Bash / Cloud Shell:

```bash
gcloud builds submit . --project="$PROJECT_ID" \
  --config=cloudbuild.yaml \
  --substitutions="_SOURCE_DIR=.,_PROJECT_ID=$PROJECT_ID,_LOCATION=$REGION,_REPOSITORY=$REPOSITORY,_JOB_NAME=$JOB_NAME,_SERVICE_ACCOUNT=$JOB_SA,_ALLOWED_INPUT_BUCKETS=YOUR_COUNTER_INPUT_BUCKET,_OUTPUT_GCS_ROOT=gs://YOUR_OUTPUT_BUCKET/counter/results,_HF_SECRET=$HF_SECRET"
```

Para un activador cuyo contexto sea el repositorio padre, seleccionar `counter_audio_transcription/cloudbuild.yaml` y mantener `_SOURCE_DIR=counter_audio_transcription`, como en Promotores. Configurar las mismas sustituciones en el activador. Para varios buckets, usar un delimitador alternativo de `--substitutions` de gcloud o ingresarlos desde la consola.

Prerrequisitos: APIs habilitadas, repositorio Docker, buckets, cuentas de servicio y secreto HF con versión `1` existente. El build necesita escritura en Artifact Registry, permisos de despliegue de Cloud Run y `iam.serviceAccounts.actAs` sobre la cuenta del Job. La cuenta del Job necesita acceso a GCS y al secreto. El flujo no crea buckets, secretos, tablas ni asignaciones IAM, y no ejecuta audios.

La configuración de modelos se toma de `config/job.env.example.yaml`; el script reemplaza proyecto, buckets permitidos y ruta de salida con las sustituciones. Los archivos `*.local.yaml` siguen excluidos del envío y no son leídos por Cloud Build. Para BigQuery opcional, completar las claves comentadas en la plantilla sin incluir secretos. Se conservan CPU=4, memoria=16Gi, timeout=7200s y reintentos=0; son valores iniciales de evaluación. El build no despliega la función HTTP opcional.

Primera ejecución directa, después de completar tus valores:

```bash
gcloud run jobs execute "$JOB_NAME" --project="$PROJECT_ID" --region="$REGION" \
  --update-env-vars="AUDIO_URI=gs://YOUR_COUNTER_INPUT_BUCKET/path/audio.m4a,RECORDING_ID=counter_000123,FORCE=false" \
  --wait
```

Cuando el job funcione, desplegar la función:

```bash
gcloud functions deploy "$FUNCTION_NAME" --gen2 --project="$PROJECT_ID" \
  --region="$REGION" --runtime=python311 --source=. \
  --entry-point=trigger_counter --trigger-http --no-allow-unauthenticated \
  --service-account="$FUNCTION_SA" --memory=256Mi --timeout=60s \
  --env-vars-file=config/function.local.yaml
```

Antes de invocarla, asignar a la cuenta de la función los permisos de ejecución con overrides del job. La función usa el endpoint Jobs v2 `:run`, solicita una única tarea y devuelve HTTP 202 con `operation_name`. Ese identificador es una **operación de lanzamiento, no el ID de la ejecución ni una confirmación de transcripción finalizada**.

Invocación autenticada, usando el JSON de ejemplo ya completado:

```bash
FUNCTION_URL="YOUR_DEPLOYED_FUNCTION_URL"
curl --fail-with-body -X POST "$FUNCTION_URL" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  --data-binary @samples/request.example.json
```

Consultar la operación por API o las ejecuciones del job en consola. Si el lanzamiento agota el timeout HTTP, verificar primero las ejecuciones: el job puede haberse aceptado. El worker evita inferencia duplicada mediante el lock por clave y el resultado completado.

## Resultados, estados y reprocesamiento

Dentro de `OUTPUT_GCS_ROOT` se genera una carpeta por `processing_key`, hash de URI/generación, recording_id y configuración del pipeline/modelos. Reutilizar el mismo recording_id para una misma grabación. `force` no modifica la clave.

```text
counter/results/<processing_key>/
  processing.lock                  # existe durante el procesamiento
  status.json                      # processing / completed / failed
  completed.json                   # referencia al último resultado publicado
  attempts/<attempt_id>/result.json
  attempts/<attempt_id>/transcript.txt
```

La descarga queda fijada a la generación del objeto GCS que existía al iniciar. Una nueva generación o configuración genera otra clave. Tras actualizar dependencias o revisiones de modelos, incrementar `PIPELINE_VERSION`; las versiones de paquetes quedan registradas, pero no entran automáticamente a la clave. Para reproducibilidad final se deben congelar versiones de paquetes y revisiones/pesos tras validar el build.

Un reproceso escribe salidas en una ruta de intento nueva y cambia `completed.json` solo después del éxito. Si falla, el resultado previo permanece. El estado puede indicar que el último intento falló aunque exista un resultado previo válido; revisar ambos archivos. `force=true` solicita nueva inferencia. Repetir una petición completada con `force=false` reutiliza el resultado y sincroniza BigQuery si está habilitado.

El lock se crea condicionalmente en GCS y se elimina condicionado a su generación. **No tiene recuperación automática ante terminación abrupta**: si el contenedor es cancelado o muere, verificar que no haya ejecuciones activas y retirar manualmente únicamente el lock de esa clave antes de reintentar. Los reintentos automáticos del job se dejan en cero durante este piloto. Una ejecución concurrente que encuentra el lock termina con error, no vuelve a procesar.

El estado `processing` comienza cuando el worker accede al audio y adquiere el lock. Una URI inexistente o un error de configuración anterior a ese punto se verá en la ejecución/logs del job; aún no habrá `status.json`. No hay estado `queued` garantizado por la función en esta versión.

## BigQuery opcional

El estado operativo de esta versión se guarda en **GCS**. No se requiere una segunda tabla de control para ejecutar el piloto. Si luego se necesita consultar estados por SQL, se puede incorporar esa integración como extensión.

Para resultados en BigQuery: crear manualmente dataset y tabla con el SQL incluido, completar `BQ_RESULTS_TABLE` y `BQ_LOCATION` y otorgar permisos. El código hace MERGE parametrizado por processing_key; no crea objetos, no borra slots ni usa tablas Genesys. Si BigQuery falla antes de publicar, la ejecución falla y puede reintentarse sin perder el resultado previo. Las salidas de intentos no publicados pueden quedar para diagnóstico.

GCS y BigQuery no forman una transacción única. Un fallo entre el MERGE y la publicación GCS puede dejar temporalmente la tabla por delante del manifiesto. El reprocesamiento idempotente vuelve a sincronizar; comprobar ambos destinos al investigar fallos.

## Validación pendiente en tu cuenta

Este paquete necesita validar instalación real de dependencias, build de imagen, descarga de modelos autorizados, permisos/secretos, normalización con FFmpeg, inferencia en español ambiental, consumo de memoria, tiempos y calidad contra escucha humana. No se han ejecutado estos pasos ni se presentan como completados.

La entrega de archivos es una base implementada y verificada con pruebas locales de lógica; no implica completar por adelantado el piloto ni las etapas de aceptación del Gantt.

## Referencias técnicas

- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Pyannote Community-1: https://huggingface.co/pyannote/speaker-diarization-community-1
- Ejecución Jobs: https://docs.cloud.google.com/run/docs/execute/jobs
- API Jobs v2: https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run
- Permisos: https://docs.cloud.google.com/iam/docs/roles-permissions/run

## Resultado de verificación local

Se ejecutó `python -m unittest discover -s tests -v` mediante el Python disponible para esta conversación. Ver informe `VERIFICACION.md`. No se instalaron dependencias en el equipo ni se ejecutaron llamadas GCP desde las pruebas.

## Despliegue desde Git

El flujo principal es Git → activador de Cloud Build → Cloud Run Job. Ver [configuración del activador](DEPLOY_TRIGGER.md). Los audios locales de samples/audios/ no se suben al repositorio.
