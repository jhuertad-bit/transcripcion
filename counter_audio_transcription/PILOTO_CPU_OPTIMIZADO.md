# Piloto CPU optimizado (v0.3.0)

Cambios: Whisper incluido en la imagen durante Cloud Build, reutilización del modelo en varios archivos por tarea, hilos CPU explícitos y tiempos por etapa. No se modifica el audio original ni se concatenan segmentos. La diarización sigue desactivada por defecto. El componente prepare sigue usando la misma imagen del Job; su separación en una imagen liviana queda para una etapa posterior.

## Piloto aislado mediante Git y Cloud Build

Antes del push, configurar el activador Counter con estos valores para desplegar recursos de prueba separados. Los nombres son propuestos: comprobar previamente que no correspondan a recursos existentes de otro uso.

| Sustitución | Valor |
| --- | --- |
| `_JOB_NAME` | `prd-utpbi-counter-audio-cpu-opt-test` |
| `_WORKFLOW_NAME` | `prd-utpbi-counter-cpu-opt-test` |
| `_OUTPUT_GCS_ROOT` | `gs://prd-utp-stg-queuesmart/counter_audio_transcription_cpu_opt_test/results` |
| `_MANIFEST_GCS_ROOT` | `gs://prd-utp-stg-queuesmart/counter_audio_transcription_cpu_opt_test/manifests` |
| `_ENABLE_DIARIZATION` | `false` |
| `_BATCH_MAX_FILES` | `3` |
| `_FILES_PER_WORKER` | `3` |
| `_PARALLELISM` | `1` |
| `_CPU` | `4` |
| `_MEMORY` | `16Gi` |
| `_WHISPER_CPU_THREADS` | `4` |
| `_WHISPER_MODEL` | `deepdml/faster-whisper-large-v3-turbo-ct2` |
| `_PROCESS_DAY_FROM` | `2026-06-01` |
| `_PROCESS_DAY_TO` | `2026-06-01` |

Conservar proyecto, región, repositorio Docker, cuentas de servicio y catálogo ya confirmados. Mantener BigQuery de resultados deshabilitado. Las cuentas necesitan acceso a los nuevos prefijos. Verificar en Cloud Build que los nombres desplegados sean los de esta tabla. No se reemplaza el Job/Workflow anterior si se usan estos nombres distintos y estaban libres.

El build descarga los pesos públicos de Whisper y su imagen será mayor. No incluye credenciales ni descarga Pyannote durante el build. El beneficio de evitar descargas al ejecutar debe compararse con el tiempo de importación de la imagen. Los pesos aún no están fijados a una revisión inmutable; congelarla tras validar el piloto.

Después del build exitoso, iniciar UNA ejecución desde Cloud Shell:

```bash
gcloud workflows execute prd-utpbi-counter-cpu-opt-test --project=prd-utpbi-data-operation --location=us-central1 --impersonate-service-account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com --data='{}'
```

Con esta configuración se seleccionan como máximo tres archivos pendientes del día indicado y se procesan secuencialmente en un solo worker. No se limita a tres minutos ni se garantiza que el día tenga tres archivos. Una ejecución no procesa automáticamente el resto del catálogo.

## Qué comparar

- Primer audio: `whisper_model_reused=false`; incluye carga desde disco local.
- Siguientes audios: `whisper_model_reused=true`; modelo reutilizado en memoria.
- `result.json`: `timings.download_seconds`, `normalize_seconds`, `whisper_model_load_seconds`, `whisper_inference_seconds`, `transcribe_seconds`, `diarize_seconds`, `alignment_seconds`.
- `status.json` completado: además, `publish_seconds` y `total_worker_seconds`. Estos tiempos comienzan dentro del worker; no incluyen programación/importación/arranque del contenedor ni prepare.
- Comparar tiempo por minuto de audio y calidad del texto. No comparar duraciones brutas entre archivos distintos como si fueran el mismo caso.

El timeout del Job es POR TAREA, compartido por todos sus archivos. Elegir lotes pequeños según duración. Si un archivo falla, se continúa con el resto y la tarea termina con error; cada resultado exitoso queda publicado independientemente. La próxima selección omite los éxitos de la misma versión/configuración. Cancelaciones abruptas pueden dejar locks que requieren la revisión manual ya documentada.

v0.3.0 genera claves nuevas: repetir el mismo audio con esta versión vuelve a transcribirlo, sin sobrescribir la versión anterior. Para actualizar solo el número de archivos por worker no es necesario cambiar PIPELINE_VERSION. Pyannote todavía no tiene caché de modelo entre archivos; con diarización activa Whisper queda en memoria, por lo que habrá que medir el pico de RAM antes de activar ese modo.

No se midió aceleración real en esta entrega: el build y el piloto quedan pendientes en GCP.
