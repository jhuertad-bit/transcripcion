# Muestra de cinco audios con diarización

En el activador conservar los recursos y prefijos de prueba. Configurar:

| Sustitución | Valor |
| --- | --- |
| `_HF_TOKEN_SOURCE` | `manual` |
| `_ENABLE_DIARIZATION` | `true` |
| `_BATCH_MAX_FILES` | `5` |
| `_FILES_PER_WORKER` | `1` |
| `_PARALLELISM` | `2` |
| `_WHISPER_CPU_THREADS` | `4` |
| `_DIARIZATION_CPU_THREADS` | `4` |
| `_CPU` | `4` |
| `_MEMORY` | `16Gi` |

Elegir un rango de fechas con al menos cinco audios disponibles. La selección es
por orden de fecha y URI, no aleatoria ni estratificada; cinco audios son una
muestra exploratoria y no garantizan representar todas las atenciones.
El nuevo parámetro de modelo cambia la clave de procesamiento; los resultados
anteriores se conservan y los audios pueden volver a procesarse.

Después de desplegar, volver a ingresar HF_TOKEN en el Job antes de ejecutar el
Workflow con {}. Con cinco audios habrá cinco tareas y como máximo dos a la vez.
El tiempo total del lote puede superar el de la prueba anterior.

Cada result.json incluye audio_identity (recording_id, URI, nombre y catálogo).
El TXT comienza con el identificador y la URI del audio. Los logs de éxito incluyen
esas referencias, las URI de resultados y los tiempos. La identidad del archivo
procesado no implica que sea una grabación completa: catalog conserva el origen
y el índice de segmento cuando existen.

Medir diarization_model_load_seconds (incluye descarga si hace falta),
diarization_input_seconds y diarization_inference_seconds; comparar tiempo por
minuto de audio y escuchar cambios de hablante. El control de hilos no garantiza
mejora hasta repetir la prueba. Pyannote se sigue cargando por audio.

Los roles todavía no se infieren: role permanece null. Para agregar clasificación
semántica, acordar los roles y el modelo disponible; conservar speaker_id y
registrar evidencia textual, origen de clasificación y casos desconocidos.
