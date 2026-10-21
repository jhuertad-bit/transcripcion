# Despliegue desde Git mediante activador Cloud Build

Flujo: commit y push a Git → activador Cloud Build → validación → build Docker → push a Artifact Registry → despliegue de Cloud Run Job.

Si counter_audio_transcription es una carpeta dentro del repositorio (como promotores_gcs_copy):
- Archivo de configuración: counter_audio_transcription/cloudbuild.yaml
- _SOURCE_DIR: counter_audio_transcription
- Filtro de archivos incluidos: counter_audio_transcription/**

Si el repositorio contiene directamente el contenido de esta carpeta:
- Archivo de configuración: cloudbuild.yaml
- _SOURCE_DIR: .
- Filtro de archivos incluidos: **

Seleccionar en el activador el repositorio y la rama acordados. Configurar las sustituciones:

| Sustitución | Valor |
| --- | --- |
| _PROJECT_ID | Proyecto GCP destino |
| _LOCATION | Región del repositorio y del Job |
| _REPOSITORY | Nombre de repositorio Docker existente en Artifact Registry |
| _SERVICE_ACCOUNT | Email de la cuenta que ejecutará el Job |
| _ALLOWED_INPUT_BUCKETS | Bucket de entrada; varios separados por coma |
| _OUTPUT_GCS_ROOT | gs://BUCKET/counter/results |
| _JOB_NAME | prd-utpbi-counter-audio-transcripcion |
| _HF_SECRET | counter-hf-token |
| _HF_SECRET_VERSION | 1 |

El secreto debe existir antes del despliegue y ser accesible a la cuenta del Job. Su valor no se guarda en sustituciones ni en Git. El titular de la cuenta Hugging Face debe aceptar las condiciones de Community-1 y crear el token de lectura.

La cuenta de Cloud Build necesita permisos de publicación en Artifact Registry, despliegue de Cloud Run y uso de la cuenta del Job (iam.serviceAccounts.actAs), además del acceso requerido a fuentes y logs. APIs y recursos deben existir previamente.

El activador despliega el Job; no procesa audios ni despliega la función HTTP opcional. Tras el despliegue se carga el audio de prueba en GCS y se ejecuta el Job con AUDIO_URI y RECORDING_ID. samples/audios/ está excluida de Git, de la carga con gcloud y del contexto Docker.
