# Despliegue de prospección

Ejecutar los comandos de esta guía desde Cloud Shell o un entorno Linux con gcloud. No se ejecutaron contra GCP durante la preparación del paquete.

## Configuración recuperada de Counter

Repositorio confirmado en la ejecución histórica: `serializa-audio-repo`, región `us-central1`. Cuenta del worker: `genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com`. Se propone reutilizar también esta cuenta para el nuevo Workflow; falta comprobar sus permisos sobre los recursos nuevos.

Estos valores ya están incorporados en cloudbuild.yaml. Counter usaba HF_TOKEN como variable manual; prospección ahora admite ese mismo modo y lo usa por defecto (`_HF_TOKEN_SOURCE=manual`). Después de CADA despliegue: Cloud Run → Jobs → prd-utpbi-prospeccion-audio-transcripcion → Editar → Variables → agregar HF_TOKEN con el token autorizado y guardar. No escribir el token en Cloud Build ni en Git. El modo Secret Manager sigue disponible con `_HF_TOKEN_SOURCE=secret`, nombre y versión del secreto.

Para el despliegue con los valores recuperados, desde dentro de esta carpeta:

```bash
gcloud builds submit . --project=prd-utpbi-data-operation --config=cloudbuild.yaml --substitutions=_SOURCE_DIR=.
```

La cuenta efectiva que ejecute Cloud Build debe tener los permisos de despliegue de esta guía; no se ha confirmado su identidad ni se han concedido permisos.

## 1. Recursos

Ya confirmados por el usuario:

- Bucket `gs://prd-utp-stg-transcripciones`, US, acceso público bloqueado.
- Dataset `prd-utpbi-data-operation.raw_transcripciones`, US.
- Las tres tablas de `sql/01...03`. Los DDL están incluidos para reproducir la estructura; no hace falta recrearlas.

Confirmar existencia y permisos de estos recursos (los valores recuperados ya figuran en Cloud Build):

- Repositorio Docker de Artifact Registry (nombre real, en `us-central1` si se usa la configuración de ejemplo).
- Cuenta de servicio del worker: `genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com`.
- Cuenta propuesta del Workflow: la misma del worker.
- Cuenta que ejecuta Cloud Build y sus permisos de despliegue.
- Opcional en modo secret: secreto `prospeccion-hf-token`, versión `1`, con un token autorizado para Pyannote. Puede reutilizarse un secreto existente cambiando las sustituciones, sin copiar el valor al código.

Cloud Run/Workflows se proponen en `us-central1`; BigQuery sigue en la multirregión `US`. No son el mismo ajuste. Los nombres nuevos de Job y Workflow son exclusivos de prospección.

Habilitar, si hace falta, las APIs de Cloud Run, Workflows, Cloud Build, Artifact Registry, Secret Manager, BigQuery y Cloud Storage. Para crear un secreto nuevo sin poner el token en el comando:

```bash
gcloud secrets create prospeccion-hf-token \
  --project=prd-utpbi-data-operation \
  --replication-policy=automatic \
  --data-file=/ruta/privada/token-hf.txt
```

El archivo privado no forma parte del paquete. Si el secreto ya existe, agregar una versión con `gcloud secrets versions add` y ajustar `_HF_SECRET_VERSION`.

## 2. Permisos

Conceder a la cuenta del worker:

| Recurso | Rol |
|---|---|
| Buckets de promotores y mentores listados en README | `roles/storage.objectViewer` |
| Bucket `prd-utp-stg-transcripciones` | `roles/storage.objectUser` (leer, escribir, actualizar y eliminar locks) |
| Dataset `raw_cita_promotor` | `roles/bigquery.dataViewer` |
| Dataset `raw_transcripciones` | `roles/bigquery.dataEditor` |
| Proyecto `prd-utpbi-data-operation` | `roles/bigquery.jobUser` |
| Secreto de Hugging Face seleccionado | `roles/secretmanager.secretAccessor` |

Conceder a la cuenta del Workflow:

- `roles/run.jobsExecutorWithOverrides` sobre el Job de prospección.
- `roles/run.viewer` sobre ese Job para consultar sus ejecuciones.
- `roles/storage.objectViewer` sobre el bucket de resultados para leer los metadatos del manifiesto.

Los overrides cambian el rol del contenedor, origen, fechas y manifiesto. El permiso para ejecutarlos está documentado en [Cloud Run IAM](https://docs.cloud.google.com/run/docs/reference/iam/roles). El Job debe existir antes de asignarle los roles de alcance Job; hacer estas asignaciones tras el despliegue y antes del primer Workflow.

La cuenta que despliega necesita crear/actualizar Cloud Run Jobs y Workflows, actuar como las dos cuentas de servicio, escribir imágenes en el repositorio y escribir logs de Cloud Build. Según la política de la organización, asignar los roles de despliegue correspondientes (`run.admin`, `workflows.admin`, `iam.serviceAccountUser`, `artifactregistry.writer`, `logging.logWriter`) en los recursos adecuados. La cuenta que inicia el Workflow necesita `roles/workflows.invoker`.

## 3. Build y despliegue

Desde el directorio que contiene `prospeccion_audio_transcription`:

```bash
gcloud builds submit prospeccion_audio_transcription \
  --project=prd-utpbi-data-operation \
  --config=prospeccion_audio_transcription/cloudbuild.yaml \
  --substitutions='_SOURCE_DIR=.,_REPOSITORY=REEMPLAZAR_REPOSITORIO,_SERVICE_ACCOUNT=REEMPLAZAR_WORKER@prd-utpbi-data-operation.iam.gserviceaccount.com,_WORKFLOW_SERVICE_ACCOUNT=REEMPLAZAR_WORKFLOW@prd-utpbi-data-operation.iam.gserviceaccount.com'
```

Solo se sube la carpeta de prospección. Reemplazar los tres valores antes de ejecutar. La validación rechaza los valores `YOUR_...` por defecto. Alternativamente configurar un activador de Cloud Build con este archivo y filtro `prospeccion_audio_transcription/**`; el repositorio debe contener esta carpeta en su raíz y en ese caso se conserva `_SOURCE_DIR=prospeccion_audio_transcription`.

Si solo se subió esta carpeta a Cloud Shell, situarse dentro de ella y usar:

```bash
gcloud builds submit . \
  --project=prd-utpbi-data-operation \
  --config=cloudbuild.yaml \
  --substitutions='_SOURCE_DIR=.,_REPOSITORY=REEMPLAZAR_REPOSITORIO,_SERVICE_ACCOUNT=REEMPLAZAR_WORKER@prd-utpbi-data-operation.iam.gserviceaccount.com,_WORKFLOW_SERVICE_ACCOUNT=REEMPLAZAR_WORKFLOW@prd-utpbi-data-operation.iam.gserviceaccount.com'
```

La imagen contiene los pesos públicos de Whisper, descargados durante el build. Pyannote se descarga al ejecutar con HF_TOKEN, agregado manualmente o mediante el secreto según el modo elegido. Se requiere acceso de salida a Hugging Face para ambas operaciones.

Recursos desplegados:

- Job: `prd-utpbi-prospeccion-audio-transcripcion`.
- Workflow: `prd-utpbi-prospeccion-audio-pipeline`.
- CPU 4, memoria 16 GiB, timeout por tarea 7200 s, sin reintentos automáticos.
- Límite inicial de 3 audios por origen, hasta 3 por tarea y paralelismo 1.

Los ajustes del activador están en `VARIABLES_ACTIVADOR.md`. Las fuentes, destinos y parámetros avanzados se definen en `config/job.env.example.yaml`. Las fechas y los orígenes se indican al ejecutar el Workflow.

## 4. Primer piloto

Empezar con promotores del 9 de noviembre de 2026:

```bash
gcloud workflows run prd-utpbi-prospeccion-audio-pipeline \
  --project=prd-utpbi-data-operation --location=us-central1 \
  --data='{"fecha_desde":"2026-11-09","fecha_hasta":"2026-11-09","tipos_origen":["promotor"]}'
```

Revisar logs, filas de intentos, transcripciones y JSON/TXT; escuchar el audio y comparar tiempos/cambios de hablante. Después ejecutar mentores:

```bash
gcloud workflows run prd-utpbi-prospeccion-audio-pipeline \
  --project=prd-utpbi-data-operation --location=us-central1 \
  --data='{"fecha_desde":"2026-11-09","fecha_hasta":"2026-11-09","tipos_origen":["mentor"]}'
```

Para ambos, omitir `tipos_origen`; las fechas son obligatorias. No repetir un origen dentro de la lista. Usar `sql/04_consultas_validacion.sql` para revisar resultados. Repetir el rango para consumir los siguientes pendientes; ajustar el tamaño después de medir duración/memoria.

El worker no ejecuta clasificación semántica. La tabla de roles quedará vacía hasta implementar y validar esa etapa y su definición de roles.
