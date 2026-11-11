# Activador exclusivo GPU

Nombre sugerido: prospeccion-transcripciones-gpu. Inicialmente manual.
En repositorio padre: prospeccion_audio_transcription_gpu/cloudbuild.yaml.
En repositorio propio: cloudbuild.yaml y _SOURCE_DIR=.
Las sustituciones completas están en cloudbuild.yaml.
No apuntar este activador al proyecto CPU ni usar el YAML antiguo.
El build despliega sin ejecutar audios. Después agregar HF_TOKEN al Job GPU.
Ver DEPLOY.md. Cuenta de build con permisos sobre nuevos recursos requerida.
