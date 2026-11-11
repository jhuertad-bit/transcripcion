# Transcripciones de prospección — GPU

Proyecto autónomo para NVIDIA L4 en europe-west4. No despliega recursos CPU.

- Build: `cloudbuild.yaml`; Docker: `worker/Dockerfile` (CUDA).
- Job: `prd-utpbi-prospeccion-audio-transcripcion-gpu`.
- Workflow: `prd-utpbi-prospeccion-audio-pipeline-gpu`.
- BigQuery: `raw_transcripciones.hist_prospeccion_audio_transcripciones_gpu`
  e `hist_prospeccion_audio_intentos_gpu`, ubicación US.
- Salidas: `gs://prd-utp-stg-transcripciones/transcripciones/prospeccion_gpu`.
- Cuota aprobada: 10 L4 en europe-west4; piloto inicial con paralelismo 1.
- Etiquetas para costos: proyecto=prospeccion, entorno=piloto, acelerador=l4.

Ver [DEPLOY.md](DEPLOY.md) para requisitos, costos, token y despliegue.
Construcción y prueba real GPU pendientes. No se han ejecutado audios.

Si esta carpeta es la raíz de su propio repositorio: `cloudbuild.yaml` y
sustitución `_SOURCE_DIR=.` en el activador. En un repositorio padre:
`prospeccion_audio_transcription_gpu/cloudbuild.yaml`, sin cambiar `_SOURCE_DIR`.
