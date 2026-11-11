# Prospección: CPU y GPU independientes

Preparado localmente. No desplegado, no ejecutado; rendimiento y compatibilidad
CUDA real pendientes del build y una prueba en NVIDIA L4. Counter no se modifica.

| Recurso | CPU existente | GPU nueva |
|---|---|---|
| Build | cloudbuild.yaml | cloudbuild.yaml |
| Dockerfile | worker/Dockerfile | worker/Dockerfile |
| Job | prd-utpbi-prospeccion-audio-transcripcion | prd-utpbi-prospeccion-audio-transcripcion-gpu |
| Workflow | prd-utpbi-prospeccion-audio-pipeline | prd-utpbi-prospeccion-audio-pipeline-gpu |
| Región Run/Workflow | us-central1 | europe-west4 |
| Dispositivo | CPU int8 | NVIDIA L4, CUDA float16 |
| Resultados GCS | transcripciones/prospeccion | transcripciones/prospeccion_gpu |
| Resultados BQ | hist_prospeccion_audio_transcripciones | hist_prospeccion_audio_transcripciones_gpu |
| Intentos BQ | hist_prospeccion_audio_intentos | hist_prospeccion_audio_intentos_gpu |

BigQuery permanece en raw_transcripciones, ubicación US. Los catálogos de promotor
y mentor son los mismos. Las claves cambian por dispositivo, precisión y versión;
procesar el mismo audio en ambas variantes genera dos resultados independientes.
No se ejecuta clasificación de roles automáticamente en esta variante.

## Configuración inicial GPU

1 NVIDIA L4 por tarea, 4 CPU, 16 GiB RAM, paralelismo 1, un audio por worker y
máximo un audio por origen/lote (si se solicitan promotor y mentor, hasta dos audios
secuenciales). Timeout 3600s por tarea. No se segmentan ni truncan audios para
encajar el timeout. Si se excede, falla y se registra el intento. GPU_MAIN comprueba
CUDA en PyTorch y soporte float16 en CTranslate2; no hay fallback silencioso a CPU.
No se añade amplificación ni filtro de ruido a prospección.

Cuota aprobada según captura: 10 L4 sin redundancia zonal en europe-west4.
Es compartida con servicios, Jobs y worker pools de ese proyecto/región; no
garantiza capacidad libre. Este despliegue usa --no-gpu-zonal-redundancy.

## Regiones y transferencia

Destino GPU: europe-west4 (Países Bajos). Artifact Registry conserva el repositorio
existente serializa-audio-repo en us-central1 con nombre de imagen GPU distinto.
Evita crear otro repositorio, pero la descarga interregional puede añadir costo.
BigQuery US se consulta mediante API con location=US; no se cambia a EU.
El bucket de resultados prd-utp-stg-transcripciones está documentado en US.
Confirmar también ubicaciones de entradas:

```bash
gcloud storage buckets describe gs://prd-utp-stg-citapromotor-9bd1430535b8 --format='yaml(location,locationType)'
gcloud storage buckets describe gs://prd-utp-stg-mentores --format='yaml(location,locationType)'
gcloud storage buckets describe gs://prd-utp-stg-transcripciones --format='yaml(location,locationType)'
```

Leer audios desde Norteamérica en Europa puede generar transferencia (tarifa
pública de referencia US$0.05/GiB). También se procesan datos en Europa: confirmar
que la política de ubicaciones del proyecto permita esa región antes del deploy.

## Desplegar sin ejecutar audios

Con la carpeta actualizada en Cloud Shell, desde prospeccion_audio_transcription_gpu:

```bash
gcloud config set auth/impersonate_service_account genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com
gcloud builds submit . --project=prd-utpbi-data-operation \
  --config=cloudbuild.yaml --substitutions=_SOURCE_DIR=.
```

Desde el repositorio padre: usar --config=prospeccion_audio_transcription_gpu/cloudbuild.yaml
y omitir _SOURCE_DIR=.; no apuntar el activador CPU al YAML GPU.
El build ejecuta pruebas, construye/publica imagen, crea las tablas GPU con sus DDL propios y despliega Job y Workflow GPU. No lanza audios ni Scheduler.
El dataset raw_transcripciones debe existir (SQL 00 si hace falta). No requiere
tablas ni despliegue de CPU: SQL 01 y 02 de esta carpeta crean directamente las
tablas GPU. El build ejecuta esos dos DDL automáticamente.

La impersonación autentica al que envía el build. Los pasos se ejecutan con la
cuenta de Cloud Build, que necesita permisos de Artifact Registry, BigQuery
(crear tablas y jobs), Cloud Run, Workflows y actuar como las cuentas de servicio.
No se conceden permisos ni se crean repositorios automáticamente. El worker necesita
lectura de catálogos/audios, escritura GCS y escritura en las nuevas tablas; el
Workflow necesita ejecutar el nuevo Job con overrides y leer manifiestos.

HF_TOKEN se mantiene manual como en CPU: después de CADA despliegue, agregarlo
en variables del Job GPU antes de ejecutar. Nunca en Git, argumentos del build ni
salidas compartidas. Alternativa: _HF_TOKEN_SOURCE=secret con secreto existente y
accesible en europe-west4 según su replicación. El build GPU no toca el token CPU.

## Primera prueba (solo cuando se decida ejecutar)

Elegir fecha con un audio conocido, una sola procedencia y comparar con CPU.
Reemplazar FECHA_REAL antes de ejecutar:

```bash
gcloud workflows execute prd-utpbi-prospeccion-audio-pipeline-gpu \
  --project=prd-utpbi-data-operation --location=europe-west4 \
  --data='{"fecha_desde":"FECHA_REAL","fecha_hasta":"FECHA_REAL","tipos_origen":["promotor"]}'
```

Revisar logs accelerator=NVIDIA L4, diarización, texto y tiempos en serialized_level_2.
No afirmar ahorro ni aumento de velocidad hasta medir. Prepare también usa el Job
GPU y factura una instancia aunque no ejecute modelos; medir esa sobrecarga.

## Costo y etiquetas

Etiquetas del Job aplicadas con CLI (incluye plantilla de ejecución):
proyecto=prospeccion, entorno=piloto, acelerador=l4, prueba=gpu-europe-west4.
En Facturación → Informes, filtrar Cloud Run y agrupar/filtrar por estas etiquetas.
Para detalle histórico por ejecución, habilitar exportación de Billing a BigQuery
y relacionar etiquetas, región y ventana temporal con las ejecuciones; no es tiempo real.
Etiquetar el Job no etiqueta automáticamente BigQuery, GCS, Artifact Registry ni
Cloud Build: sus cargos se revisan aparte. No hay corte automático de presupuesto.

Tarifa pública de referencia consultada 11/11/2026: L4 US$0.0001867/s,
CPU US$0.000018/vCPU-s y RAM US$0.000002/GiB-s. Con 4 CPU y 16 GiB:
US$1.04652 por hora de instancia GPU, antes de descuentos, franquicias, impuestos
y otros servicios. Una GPU ocupada 12 horas: US$12.56; cinco: US$62.79.
Se cobra por vida completa de la instancia (incluye cargas); paralelismo no es
duración garantizada ni costo fijo. Cuota aprobada por sí sola no reserva GPU.

Referencias oficiales:
- https://docs.cloud.google.com/run/docs/configuring/jobs/gpu
- https://docs.cloud.google.com/run/docs/configuring/jobs/labels
- https://cloud.google.com/run/pricing
- https://cloud.google.com/storage/pricing
- https://github.com/SYSTRAN/faster-whisper
