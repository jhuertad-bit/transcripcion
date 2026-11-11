# Piloto por día: Chiclayo, Chimbote y Trujillo

Preparación local; no implica despliegue ni ejecución de audios.
Se reutiliza la imagen del Job habitual sin cambiarlo. La imagen debe soportar
prepare y workers (versión del proyecto con catálogo). Conserva modelos,
diarización, recursos y paralelismo del origen. No ejecuta clasificación de roles.
El asesor registrado es metadato del ticket, no una asignación a una voz.

## 1. Crear objetos de BigQuery

Ejecutar `sql/02_piloto_sedes.sql` en BigQuery, en la ubicación del catálogo.
Crea entrada congelada para el 2–7 de noviembre, resultados y vista enriquecida.
No usa CREATE OR REPLACE: si existen, detenerse y revisar, sin borrarlos.
El script tiene varias sentencias y no es atómico: si falla, revisar los objetos
ya creados antes de continuar. Si falla ASSERT, no ejecutar el lote.
Verificar 265 filas con URI no nula y única. Totales diarios esperados:
49, 45, 55, 33, 55 y 28. Los asesores/clientes vacíos se mantienen como NULL.

## 2. Crear Job y Workflow exclusivos desde Cloud Shell

Subir esta carpeta a Cloud Shell. Ejecutar desde `counter_audio_transcription`.
Completar REGION y BQ_LOCATION con los valores reales; no asumir que coinciden.
WORKFLOW_SA es la cuenta del Workflow habitual, con permiso para ejecutar el
nuevo Job y leer manifiestos. La cuenta del Job necesita leer las dos tablas
fuente al crear la entrada, leer la nueva entrada, escribir resultados y acceder
a los prefijos GCS derivados. No se cambian permisos automáticamente.

```bash
set -euo pipefail
PROJECT=prd-utpbi-data-operation
REGION=COMPLETAR
BQ_LOCATION=COMPLETAR
WORKFLOW_SA=COMPLETAR
SOURCE_JOB=prd-utpbi-counter-audio-transcripcion
PILOT_JOB=prd-utpbi-counter-piloto-202611
PILOT_WORKFLOW=prd-utpbi-counter-piloto-202611-pipeline

gcloud run jobs describe "$SOURCE_JOB" --project="$PROJECT" --region="$REGION" --format=json > source-job.json
python3 scripts/prepare_pilot_job.py source-job.json pilot-job.json --bq-location "$BQ_LOCATION"
# Revisar pilot-job.json antes del despliegue. Puede contener configuración sensible.
# El nombre destino es exclusivo del piloto; el Job habitual no se modifica.
gcloud run jobs replace pilot-job.json --project="$PROJECT" --region="$REGION"
MANIFEST_ROOT=$(python3 -c 'import json; j=json.load(open("pilot-job.json")); e=j["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]; print(next(x["value"] for x in e if x["name"]=="MANIFEST_GCS_ROOT"))')
gcloud workflows deploy "$PILOT_WORKFLOW" --project="$PROJECT" --location="$REGION" \
  --source=workflows/piloto_sedes.yaml --service-account="$WORKFLOW_SA" \
  --set-env-vars="PROJECT_ID=$PROJECT,LOCATION=$REGION,MANIFEST_GCS_ROOT=$MANIFEST_ROOT"
```

No agregar estos archivos exportados a Git. El clon utiliza el bucket actual
con `/piloto-202611/results` y `/piloto-202611/manifests` bajo la raíz de salida
actual. Su estado de completados queda separado del proceso habitual.

## 3. Ejecutar un día

```bash
gcloud workflows run "$PILOT_WORKFLOW" --project="$PROJECT" --location="$REGION" \
  --data='{"process_day":"2026-11-02"}'
```

Esperar a que termine antes de lanzar otra ejecución. Cambiar solo la fecha,
hasta el 7 de noviembre. Se filtra process_day del catálogo, no la fecha de carga
del ticket. Máximo 100 archivos por ejecución. Repetir un día omite completados
en el piloto y vuelve a seleccionar pendientes; no lanzar lotes solapados.
Si falla una etapa de workers, los éxitos se conservan y se puede repetir el día.

## 4. Revisar avance

```sql
SELECT process_day, sede, COUNT(*) AS procesados,
  COUNTIF(asesor_nombre IS NULL) AS sin_nombre_asesor
FROM `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_consulta`
GROUP BY process_day, sede
ORDER BY process_day, sede;
```

Consultar la vista para texto, sede, asesor y cliente. Las tablas originales,
el Workflow habitual y el código del motor permanecen sin cambios.
