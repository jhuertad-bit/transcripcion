# Variables del activador de prospección

Los cuatro valores de esta tabla ya vienen en `cloudbuild.yaml`. Agrégalos al activador si quieres cambiarlos desde la consola. Si duplicaste Counter, elimina sus sustituciones heredadas para usar los valores de prospección.

| Variable | Valor inicial |
|---|---|
| `_BATCH_MAX_FILES` | `3` |
| `_ENABLE_DIARIZATION` | `true` |
| `_FILES_PER_WORKER` | `3` |
| `_PARALLELISM` | `1` |

Las cuentas, la región, los recursos y el modo de token tienen valores en `cloudbuild.yaml`. BigQuery, los buckets y los ajustes avanzados están en `config/job.env.example.yaml`. Las fechas y los orígenes se indican al ejecutar el Workflow.

Ejecuta un build después de cambiar una sustitución. Con diarización activa y modo manual, agrega `HF_TOKEN` al Job después del despliegue.
