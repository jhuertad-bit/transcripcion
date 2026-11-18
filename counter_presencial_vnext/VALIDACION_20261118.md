# Validación de la entrega — 18 de noviembre de 2026

## Resultado local

- 72 pruebas offline de VNext aprobadas: aislamiento de recursos, contrato
  `JOB_ROLE`, orquestación sobre un único Job, roles, integridad de resultados,
  checkpoints parciales y errores del proceso Canary.
- 72 pruebas del proyecto original aprobadas. La carpeta
  `counter_audio_transcription/` conserva su contenido anterior.
- Instalación limpia de ambos entornos en Python 3.12.14 y comprobación real
  con `pip check`: sin dependencias rotas.
- Pipeline: Torch/Torchaudio 2.8.0, Transformers 5.18.0.dev0 del commit
  `07338b6c74a578868368e6e549dea83414e4b8cb`, Hugging Face Hub 1.33.0 y
  Tokenizers 0.23.2. Imports y registro de Nemotron comprobados; modelo español
  de spaCy cargado.
- Canary: NeMo 2.7.3, Transformers 4.57.6 y Torch/Torchaudio 2.8.0.
  Imports de ASRModel/EncDecMultiTaskModel y API de timestamps comprobados.
- Sintaxis Python, rutas COPY de Docker, los cuatro directorios de Cloud Build,
  referencias entre pasos del Workflow y sintaxis Bash de los scripts generados
  comprobados.
- Revisión directa de la segmentación y atribución contextual de las tres
  ramas acumulativas de SAXO. Se incorporaron las funciones originales de
  formación de candidatos y seis pruebas de su contrato: puntuación Unicode,
  pausas, repeticiones, contexto global sin speaker fiable, dos clientes
  acústicos distintos y bloques mixtos sin alineación.
- 200 comparaciones deterministas con la función de segmentación del fuente
  SAXO: coincidencia de texto, tiempos, número de palabras y motivo del corte;
  conservación exacta de todas las palabras, incluidas repeticiones.

Las pruebas de recuperación incluyen reutilización de ventanas completadas,
rechazo de checkpoints corruptos, ausencia de voz sin llamadas a los modelos,
fallos/timeout del proceso Canary y rechazo de más de cuatro hablantes
significativos. Los tests verifican que los modelos GPU se descargan antes de
cargar el siguiente.

## Validación en GCP

Cloud Build completó los cuatro pasos del build `8c496026…` del commit
`e6408d3`, incluida la compilación de las imágenes CPU y GPU. La publicación
falló al buscar el repositorio anterior. VNext ahora utiliza el repositorio
existente de Counter, `serializa-audio-repo`, y la subruta
`counter-presencial-vnext/`. Falta confirmar la publicación con un build
del commit actualizado, la descarga de pesos, la inferencia en L4 y Gemini.

Paul debe confirmar el build nuevo, cuotas/permisos, revisiones inmutables de
los pesos y endpoint Gemini; después, ejecutar el piloto de hasta tres
conversaciones completas y revisar texto, timestamps, hablantes y roles.
Los comandos y el orden están en README.md y PARA_PAUL.md.

El activador compila imágenes. Crear/configurar el Job y cambiar su imagen
requiere seguir esos pasos explícitos; un push por sí solo no actualiza el Job.
