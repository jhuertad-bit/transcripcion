# Prueba de dos audios con diarización sin Secret Manager

El worker ya lee HF_TOKEN del entorno. El modo manual permite desplegar sin
Secret Manager. No incluir el token en Git ni en sustituciones de Cloud Build.

Conservar proyecto, región, cuentas, catálogo y buckets válidos. Usar los nombres
y prefijos aislados de PILOTO_CPU_OPTIMIZADO.md y cambiar estas sustituciones:

| Sustitución | Valor |
| --- | --- |
| `_HF_TOKEN_SOURCE` | `manual` |
| `_ENABLE_DIARIZATION` | `true` |
| `_BATCH_MAX_FILES` | `2` |
| `_FILES_PER_WORKER` | `1` |
| `_PARALLELISM` | `2` |
| `_CPU` | `4` |
| `_MEMORY` | `16Gi` |
| `_PROCESS_DAY_FROM` / `_PROCESS_DAY_TO` | Rango con dos audios pendientes |

_HF_SECRET y _HF_SECRET_VERSION no se usan en modo manual.
El modo predeterminado sigue siendo secret para los despliegues existentes.

## Después del build y antes de ejecutar

1. Abrir Google Cloud > Cloud Run > Jobs > el Job de prueba desplegado.
2. Editar el contenedor, variables y secretos.
3. Agregar una variable de entorno normal HF_TOKEN con tu token de Hugging Face.
4. Guardar. Necesitas permiso para actualizar el Job.
5. Aceptar las condiciones de pyannote/speaker-diarization-community-1 con la cuenta del token y autorizar el acceso al modelo.
6. Ejecutar una vez el Workflow de prueba con entrada {}.

IMPORTANTE: --env-vars-file reemplaza las variables del Job. Volver a agregar
HF_TOKEN después de CADA despliegue de Cloud Build y antes de ejecutar.
El token queda visible para quienes puedan leer la configuración del Job.
Retirarlo al terminar la prueba. No pegarlo en logs ni comandos del build.

Se seleccionan hasta dos audios pendientes. Con dos archivos se crean dos tareas,
una por audio, con paralelismo máximo dos. Dentro de cada tarea se transcribe y
después se diariza. Revisar ambos resultados en OUTPUT_GCS_ROOT, que indiquen
`diarization_status: completed`, sus hablantes y tiempos por etapa.
La inferencia real queda pendiente hasta desplegar, ingresar el token y ejecutar.