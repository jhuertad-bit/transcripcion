# Verificación de componentes Counter

Fecha: 20/10/2026.

## Ejecutado localmente

- **17 pruebas automatizadas aprobadas**, sin llamadas GCP ni inferencia.
- Validación sintáctica de los **15 archivos Python** mediante AST.
- Lectura y validación sintáctica del JSON de solicitud de ejemplo.
- Revisión de los contratos de Community-1, faster-whisper y Jobs v2 frente a documentación oficial.
- Selección de un conjunto explícito de PyTorch 2.8.0, torchaudio 2.8.0 y TorchCodec 0.7.0, compatible según la matriz publicada de TorchCodec; el build efectivo aún debe validarse.

Comando de pruebas desde la raíz del proyecto:

```bash
python -m unittest discover -s tests -v
```

Cobertura de comportamiento: validación del JSON y buckets; parámetros de ejecución; HTTP 202 con operación de lanzamiento; cambio de hablante; empates y falta de voz; poca intersección; solapamiento real frente a intervenciones consecutivas; timestamps inválidos; separación por pausas; persistencia e idempotencia; fallo de un reproceso sin perder resultado anterior; fallo de BigQuery sin publicar manifiesto de éxito; cambio de generación/configuración; sincronización posterior de BigQuery sin repetir inferencia.

Las pruebas del pipeline simulan repositorio, audio y modelos. La primera ejecución encontró una restricción de eliminación del directorio temporal del sandbox de Windows; se ajustó el arnés de pruebas para usar un contexto temporal simulado, coherente con el resto de E/S simulada. No se debilitó el manejo de temporales del worker productivo. La ejecución final terminó con código 0.

## Pendiente de ejecutar en la cuenta del usuario

- Resolución completa de dependencias e instalación real dentro de la imagen Linux.
- Build y despliegue del job y de la función HTTP.
- Descarga de los pesos con acceso autorizado y token HF.
- Validación real de FFmpeg y ambos modelos con muestras ambientales Counter.
- IAM, secreto, acceso a GCS, bloqueo condicional y recuperación tras cancelación real.
- MERGE en BigQuery, si se habilita esa salida.
- Calidad de palabras, diarización, tiempos de procesamiento y memoria.
- Aceptación del usuario sobre audio real y ajuste del dimensionamiento.

No se ejecutaron comandos de creación/despliegue ni se crearon objetos en GCP. No se copiaron llaves ni tokens. No se instalaron dependencias nuevas ni se modificó Genesys/Markov.

El paquete es una implementación inicial para validar; estas pruebas locales no certifican un despliegue ni sustituyen el piloto de audio.

Referencias: [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Jobs v2](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run), [matriz TorchCodec](https://github.com/pytorch/torchcodec#compatibility-with-torch-versions).


## Actualización 27/10/2026: catálogo y lotes

- 23 pruebas locales aprobadas, con E/S/modelos simulados; incluye relación entre segmentos, validación de manifiesto y configuración, selección de pendientes antes del límite, y persistencia de metadatos.
- Validación AST de todos los archivos Python.
- Cloud Build incorpora las pruebas antes del build Docker y despliega también el Workflow prepare/worker.
- No se ejecutaron Cloud Build, despliegues ni inferencia real. La compilación semántica de Workflows, ejecución del script Bash, resolución de dependencias y acceso real a BigQuery/GCS siguen pendientes en GCP. No hay parser YAML disponible en el runtime local utilizado.


## Versión 0.2.0: solo transcripción

25 pruebas locales aprobadas con modelos/E/S simulados. Se verifica omisión de la llamada a diarización, texto sin etiquetas de hablantes, tiempos conservados, estado disabled y clave diferente al cambiar de modo. Validación AST Python. No se ejecutaron build, despliegue ni inferencia real; la validación Bash/Workflows en GCP continúa pendiente.


## v0.3.0: CPU optimization

29 local tests pass with simulated I/O and models. New coverage: exact partition across tasks, continuation after individual failure, bundled model path, one model load for two files, cache invalidation on model change. All Python files parsed with AST. Cloud Build, actual bundled image import time, model inference, Workflows validation and performance measurement remain pending in GCP. No deployment or IAM change executed.
