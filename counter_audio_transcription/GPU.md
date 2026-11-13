# Counter GPU independiente

Preparado localmente; pendiente de build, despliegue y prueba real L4.
No cambia el Job CPU ni el Workflow nocturno. Se basa en la configuración del
Job piloto prd-utpbi-counter-piloto-202611, no en el Job habitual.

- Job nuevo: prd-utpbi-counter-piloto-202611-gpu, europe-west4.
- Imagen: counter-audio-transcription-gpu, en el repositorio existente us-central1.
- Una L4, 4 CPU, 16 GiB, timeout 7200s, paralelismo inicial 1.
- Conserva catálogo, filtro de ruido, modelos y ubicación BigQuery del piloto CPU.
- Tabla de resultados CPU más sufijo _gpu. Si hay tabla de roles, crea también
  su esquema vacío con sufijo _gpu. No se copian datos CPU.
- Rutas GCS CPU más /gpu. Claves de procesamiento distintas por cuda/float16.
- HF_TOKEN se agrega manualmente al Job nuevo; no se copia ni imprime.
- No despliega Workflows, Scheduler ni inicia audios. La clasificación de roles
  sigue siendo una etapa aparte en BigQuery; L4 acelera Whisper y Pyannote.

## Build

Subir los archivos nuevos manteniendo el activador CPU inhabilitado para este
cambio. Desde el repositorio padre en Cloud Shell, con gcloud autenticado:

```bash
gcloud builds submit counter_audio_transcription \
  --project=prd-utpbi-data-operation \
  --config=counter_audio_transcription/cloudbuild.gpu.yaml \
  --service-account=projects/prd-utpbi-data-operation/serviceAccounts/genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com \
  --impersonate-service-account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com
```

## Despliegue

Tras un build SUCCESS, sustituir ID_BUILD_GPU:

```bash
python3 counter_audio_transcription/scripts/deploy_gpu.py --build-id ID_BUILD_GPU
```

El script usa impersonación explícita. Lee la configuración real del piloto CPU
en memoria sin guardarla ni mostrarla, crea tablas vacías y despliega solo el
Job nuevo. Requiere permisos para describir el origen, crear tablas/jobs BQ,
desplegar Cloud Run y actuar como la cuenta de ejecución.

Agregar HF_TOKEN al Job GPU después del despliegue y antes de una prueba.
Volver a desplegar con este script reemplaza variables: verificar el token.
Para una comparación válida, elegir un audio CPU y ejecutarlo en GPU con su
mismo catálogo y filtro de ruido. Los resultados GPU no completan las tablas CPU.

La cuota L4 en europe-west4 es compartida con prospección y otros recursos.
Tras validar un audio, se puede cambiar a paralelismo 5: coordinar el consumo
simultáneo con prospección. Cuota no garantiza capacidad disponible.
La entrada/salida puede cruzar US-Europa; contemplar transferencia y ubicación
de procesamiento. No estimar costo ni mejora hasta medir el audio Counter.
