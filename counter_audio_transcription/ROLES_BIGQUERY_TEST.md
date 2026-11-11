> Casos de revisión: al publicar, Desconocido siempre requiere revisión. Si la
> diarización completada no identifica voces, se guarda una marca de revisión
> de la grabación (speaker_id null, scope recording), sin invocar Gemini ni
> inventar voces. El estado completed significa que la etapa terminó, no que
> la calidad del audio o los roles hayan sido aprobados. Los fallos técnicos
> y las respuestas con speakers inventados siguen fallando.

> Evidencia inválida: al publicar, solo el speaker afectado se marca
> Desconocido y requires_review=true. Se conservan proposed_role,
> proposed_evidence y validation_issues para revisión, sin aceptar las citas
> inválidas. El archivo queda completed con revisión pendiente; completed no
> significa que todos los roles estén determinados. No se relajan controles
> de speakers faltantes, inventados o duplicados. El prompt sigue en v2:
> los éxitos previos se conservan y se reintentan los fallos del mismo lote.

> Prompt v2: Gemini selecciona segment_id como evidencia. El programa valida
> que existan y pertenezcan al speaker y copia texto y tiempos originales.
> Se vuelve a clasificar la muestra completa con counter-roles-v2; v1 permanece
> disponible. Filtrar la vista por prompt_version para evitar mezclar versiones.

> Corrección de importación: PARSE_JSON usa wide_number_mode => 'round' para
> números de punto flotante como 0.9215774536132812. El JSON original de GCS se
> conserva; BigQuery almacena la representación numérica compatible. Se aplica
> también a las evidencias de roles. El error se confirmó con una carga del
> archivo original, antes de Gemini. Los errores previos pueden reintentarse.

## Modelo remoto existente (ruta preferida del piloto)

Se verificó una llamada manual con el usuario mediante AI.GENERATE_TABLE al
modelo prd-utpbi-data-operation.adf_speech_analytics.gemini-3-5-flash.
Esto aún no confirma los permisos de la cuenta de servicio del flujo.
Configurar en el activador:

_ROLE_MODEL=prd-utpbi-data-operation.adf_speech_analytics.gemini-3-5-flash
_ROLE_CONNECTION= (vacío; el modelo remoto usa su propia conexión)

Desplegar el worker actualizado antes de repetir roles_only con la URI del
manifiesto ORIGINAL de cinco archivos; no usar el ID de una ejecución de roles
para buscar un manifiesto nuevo. No se recrea ni modifica el modelo compartido.
Los resultados guardan la ruta completa del modelo; filtrar la vista por ese
valor y prompt_version=counter-roles-v1. Las consultas que filtran el endpoint
simple gemini-3.5-flash deben actualizar ese filtro para ver esta prueba.

# Roles dinámicos: piloto desde Cloud Shell

Estado: implementación local, sin ejecución de SQL ni llamadas a Gemini en GCP.
Roles: Asesor, Cliente (incluye acompañantes) y Desconocido. Un speaker puede tener
cualquier rol en cada archivo; varios speakers pueden compartir rol.

## Primera prueba: no requiere build ni token HF

Subir el paquete counter_roles_test.zip a Cloud Shell con Subir archivo.
Este paquete solo contiene el ejecutor de roles, dependencias ligeras, SQL y el
manifiesto de los cinco archivos ya procesados. No contiene audios ni tokens.

```bash
unzip -o counter_roles_test.zip -d counter_roles_test
cd counter_roles_test
bash scripts/roles_cloudshell.sh setup
bash scripts/roles_cloudshell.sh import
bash scripts/roles_cloudshell.sh run
```

setup crea dos tablas y una vista en el dataset existente raw_queue_smart (US):
- counter_transcripciones_roles_test: copia de los resultados publicados.
- counter_roles_test: clasificaciones versionadas por processing_key, modelo y prompt.
- counter_roles_test_segmentos: segmentos unidos con roles, sin alterar los JSON de GCS.

import solo lee completed.json y el resultado publicado de cada clave calculada
desde el manifiesto. No lista ni selecciona resultados de otras pruebas. Verifica
identificador, fuente y clave. Copia a BigQuery con MERGE. No ejecuta modelos.

run importa de forma idempotente y clasifica los cinco archivos, una solicitud
por conversación; omite éxitos del mismo modelo/prompt. Los fallos se registran
y pueden reintentarse con run. No se ejecutan Whisper, Pyannote ni el Workflow
de audio. Son cinco segmentos/archivos de tres originales, no cinco originales.
El prompt completo está en worker/roles.py (PROMPT_VERSION=counter-roles-v1).

La autenticación usa gcloud con la misma cuenta impersonada del piloto. No hace
falta llave ni token HF. Necesita lectura de GCS, creación/lectura/escritura de las
tablas y vista de prueba, jobs de BigQuery y permisos de inferencia de Gemini.
El script no concede permisos ni habilita APIs. Si setup/import falla, conservar
la salida del error. Si Gemini falla, revisar el job SQL en BigQuery y la tabla
de roles. Una cita inexistente, speaker inventado o respuesta incompleta produce
estado failed; no se acepta silenciosamente como clasificación.

Si el entorno requiere conexión BigQuery para la inferencia, usar una conexión
existente y autorizada en US:
```bash
export ROLE_CONNECTION='prd-utpbi-data-operation.us.NOMBRE_CONEXION'
bash scripts/roles_cloudshell.sh run
```
La cuenta de esa conexión necesita permisos de inferencia. Confirmar esta ruta
de autenticación para automatización antes de activar la etapa en el Workflow.
No se garantiza acceso al modelo hasta ejecutar la prueba real.

## Revisar

Ejecutar sql/roles_test_revision.sql en BigQuery. Para mostrar desde Cloud Shell:
```bash
bq --project_id=prd-utpbi-data-operation --location=US \
  --impersonate_service_account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com \
  query --use_legacy_sql=false \
  'SELECT recording_id, audio_uri, status, roles, error_type FROM `prd-utpbi-data-operation.raw_queue_smart.counter_roles_test` ORDER BY updated_at DESC'
```
El estado completed indica respuesta estructuralmente válida, no precisión
garantizada. Verificar a oído los roles y revisar requires_review. Las citas se
validan literalmente contra el texto y speaker originales. Tramos sin speaker
permanecen Desconocido. En la vista, si existen varias versiones, filtrar por
modelo y prompt_version. La clasificación automática no reemplaza una revisión
manual almacenada aparte.

## Integrar después de validar el piloto

El código completo está también en LISTO_PARA_GIT. El activador mantiene roles
desactivados por defecto. Para el flujo de prueba configurar:
```
_BQ_RESULTS_TABLE=prd-utpbi-data-operation.raw_queue_smart.counter_transcripciones_roles_test
_BQ_LOCATION=US
_BQ_ROLES_TABLE=prd-utpbi-data-operation.raw_queue_smart.counter_roles_test
_ROLE_MODEL=gemini-3.5-flash
_ROLE_CONNECTION=CONEXION_AUTORIZADA_SI_CORRESPONDE
_ENABLE_ROLE_CLASSIFICATION=true
```
Usar las tablas creadas con setup. Desplegar por Cloud Build. Volver a ingresar
HF_TOKEN al Job después del build si sigue en modo manual.

El Workflow invoca JOB_ROLE=roles después de workers exitosos. La etapa usa una
sola tarea y no carga modelos de audio, aunque reutiliza la imagen existente.
Un fallo de roles falla el Workflow conservando los audios procesados. Se puede
repetir solo roles enviando al Workflow:
```json
{"roles_only":true,"manifest_uri":"gs://BUCKET/manifests/ID_DEL_LOTE.json"}
```
Esta ruta funciona tras desplegar la nueva versión y configurar las tablas.
No cambia el TXT base de GCS; la vista es la transcripción enriquecida con roles.
Para cambiar instrucciones, incrementar PROMPT_VERSION antes de reprocesar.

Referencias: https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-generate
y https://docs.cloud.google.com/bigquery/docs/permissions-for-ai-functions
