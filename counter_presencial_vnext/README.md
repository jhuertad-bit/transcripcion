# Counter Presencial VNext — compatible con el workflow de Paul

Esta entrega conserva el contrato de orquestación de Counter: **un Job nuevo**
con `JOB_ROLE=prepare → worker → roles`, manifiestos en GCS, BigQuery y
reanudación de roles. En `prd-utpbi-data-operation` usa sus propios recursos de
ejecución y una subruta para sus imágenes en el repositorio Docker de Counter.

La implementación de audio toma como referencia las tres ramas de SAXO
Presencial. Ver [SAXO_COMPATIBILITY.md](SAXO_COMPATIBILITY.md) para los commits,
las decisiones conservadas y los límites de la validación.

## Qué contiene

- Nemotron con el Transformers fijado por SAXO, canales significativos y límite
  de cuatro; un quinto canal significativo produce un error revisable.
- Ventanas de voz y limpieza conservadora de bucles tomadas de SAXO.
- Canary 1B v2 con NeMo **2.7.3** y timestamps del CTC auxiliar, en otro entorno
  Python dentro de la misma imagen. Las dos versiones de Transformers no se mezclan.
- Nemotron se descarga de VRAM antes de cargar Canary. El proceso de NeMo se
  cierra antes de volver a cargar Nemotron; no hay dos modelos GPU residentes.
- Gemini sobre el diálogo completo, reglas de contacto/seguimiento V3.1
  adaptadas, fusión y revisión selectiva. Los números de confianza de JEV no
  se trasladan a Gemini.
- Checkpoints de diarización y por ventana: un reintento reutiliza lo terminado.
- Audios sin actividad significativa no invocan Canary ni Gemini; el resultado
  conserva la razón del descarte. Es una política acústica, no verdad de referencia.

## Compilación: primer paso para Paul

Ver [PARA_PAUL.md](PARA_PAUL.md). Reemplazar únicamente esta carpeta en el repo,
incluyendo los archivos nuevos, y hacer push al activador propio existente.

| Configuración | Valor |
|---|---|
| YAML | `counter_presencial_vnext/cloudbuild.yaml` |
| `_SOURCE_DIR` | `counter_presencial_vnext` |
| `_REGION` | `us-central1` |
| `_REPOSITORY` | `serializa-audio-repo` |
| Filtro incluido | `counter_presencial_vnext/**` |

El build publica en Artifact Registry bajo `serializa-audio-repo`, en las
subrutas `counter-presencial-vnext/cpu` y `counter-presencial-vnext/gpu`. El Job único
usa la imagen GPU, que incluye las dependencias de preparación y roles.
La imagen CPU permite diagnósticos o ejecución CPU explícita de esas etapas.

Para compilar, la cuenta elegida en el activador
`deploy-counter-presencial-vnext` debe tener permiso de escritura en
`serializa-audio-repo`. Si se usa `generated/build.sh`, comprobar el mismo
permiso para la cuenta `cpv-build` que usa ese script.

## Recursos propios

| Recurso | Nombre |
|---|---|
| Job único | `counter-presencial-vnext-gpu` |
| Modos del Job | `prepare`, `worker`, `roles` |
| Cuenta del Job | `cpv-pipeline@PROJECT_ID.iam.gserviceaccount.com` |
| Otras cuentas | `cpv-build`, `cpv-workflow`, `cpv-operator` |
| Buckets | `PROJECT_ID-cpv-input`, `PROJECT_ID-cpv-output`, `PROJECT_ID-cpv-builds` |
| Prefijo de salida | `gs://PROJECT_ID-cpv-output/presencial-vnext` |
| Dataset | `PROJECT_ID.counter_presencial_vnext` |
| Catálogo | `catalog_vnext` |
| Tabla / vista | `counter_presencial_resultados_vnext`, `counter_presencial_segmentos_vnext` |
| Modelo remoto / conexión | `gemini_roles_vnext` / `counter-presencial-vnext-gemini` |
| Workflow opcional | `counter-presencial-vnext` |

No reutilizar Jobs, tablas, cuentas, conexión Gemini ni buckets de Paul. Copiar
los audios y metadatos al catálogo propio. Las cuotas por proyecto y región
sí se comparten en PRD: coordinar la prueba con Paul.

## Configurar y ejecutar

1. Copiar `deployment.example.json` a `deployment.local.json`. Completar los
   commits de los dos modelos, el endpoint Gemini disponible en PRD y las fechas.
   Revisar el inventario de recursos exclusivos y establecer
   `isolated_resources_confirmed=true`.
2. Ejecutar `python3 scripts/render_deployment.py deployment.local.json`.
   Solo genera archivos locales; no contacta GCP. Si ya existe `generated/`
   de una entrega anterior, conservarlo aparte y generar uno nuevo para evitar
   ejecutar los antiguos scripts de tres Jobs.
3. Revisar `generated/inventory.json` y `bootstrap.sh`. En PRD el script
   comprueba la existencia del repositorio de Counter y crea los recursos
   propios de ejecución. Si algún recurso propio ya existe, aplicar solamente
   los pasos pendientes.
4. Compilar mediante el activador existente o `bash generated/build.sh`.
5. Exportar `CPV_GPU_DIGEST=sha256:...` del build aprobado y ejecutar
   `bash generated/create_jobs.sh`. Crea solo el Job nuevo; no actualiza un
   Job preexistente ni requiere el digest CPU. Si el nombre ya existe, describirlo
   y comprobar su propiedad antes de decidir una actualización.
6. Dar al operador humano Token Creator sobre `cpv-operator`. El Job usa
   `cpv-pipeline` con permisos de datos únicamente sobre nuestros destinos.
7. Copiar los audios completos a nuestro bucket, cargar el catálogo y ejecutar:

```bash
bash generated/run_manual.sh
# Reintentar worker y roles del mismo lote, sin prepare:
bash generated/run_manual.sh ID_DEL_LOTE
# Repetir solo Gemini/publicación:
bash generated/run_manual.sh --roles-only ID_DEL_LOTE
```

El script se detiene si worker falla; no continúa a roles como si el audio
hubiera terminado. Los checkpoints permiten reintentar lo pendiente.
Un único Job mantiene la GPU asignada también durante prepare/roles; se
conserva este esquema para respetar el workflow operativo de Paul.

El Workflow es opcional para el primer piloto. `generated/optional_workflow.sh`
crea el nuestro si no existe; nunca reemplaza otro Workflow. Su grafo conserva
prepare, manifiesto, worker, roles, `ROLE_ENABLED` y `roles_only` del original,
con una comprobación adicional que rechaza un JOB_NAME diferente del nuestro.

## Modelo y conversación

Usar grabaciones completas. Para fragmentos se necesitan offsets reales,
índices contiguos y `recording_complete=true`, o un mapa de originales en
nuestro bucket. Sin esos datos, queda `needs_source_review`; no se inventa continuidad.
El manifiesto fija generaciones GCS y revisiones de modelos, y el reintento
rechaza otra configuración. Un cambio de esta versión invalida las cachés
acústicas antiguas para no mezclar políticas.

## Verificación y límites

```bash
python -B -m unittest discover -s tests
```

Las pruebas offline cubren el contrato de Paul, contexto global, fusión,
procesos, timeouts, silencios, bucles, checkpoints e independencia.
Cada entorno tiene restricciones de dependencias propias y ejecuta imports
reales/API checks durante el build. No se usa `--no-deps` para ocultar conflictos.

**Pendiente:** publicación de las imágenes en `serializa-audio-repo`,
restauración de pesos e inferencia en L4, permisos/cuotas y piloto sobre
audios reales. Ver el detalle del flujo en [PIPELINE.md](PIPELINE.md).
