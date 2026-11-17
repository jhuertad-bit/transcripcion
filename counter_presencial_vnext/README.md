# Counter Presencial VNext — entrega independiente

Este directorio es un proyecto ejecutable por sí solo. Se entrega para publicarlo
como raíz de un **repositorio GitHub nuevo** y desplegarlo en un **proyecto GCP
nuevo y dedicado**. El repositorio actual solo permite revisar la propuesta.
No hay recursos creados ni despliegues ejecutados.

El proyecto original de Paul permanece byte por byte como en el ZIP recibido.
Esta carpeta contiene el procesamiento Canary + Nemotron + roles contextuales con
Gemini, sus utilidades locales, pruebas, imágenes y configuración propias.
No importa ni necesita ejecutar componentes del proyecto original.

## Recursos propios

`PROJECT_ID` será el ID real del proyecto nuevo que habilite Paul.

| Componente | Nombre o destino nuevo |
|---|---|
| Job de preparación CPU | `counter-presencial-vnext-prepare` |
| Job de audio GPU | `counter-presencial-vnext-gpu` |
| Job de roles CPU | `counter-presencial-vnext-roles` |
| Activador Cloud Build | `counter-presencial-vnext-build` |
| Repositorio Artifact Registry | `counter-presencial-vnext` |
| Imágenes | `counter-presencial-vnext-cpu`, `counter-presencial-vnext-gpu` |
| Buckets | `PROJECT_ID-cpv-input`, `PROJECT_ID-cpv-output`, `PROJECT_ID-cpv-builds` |
| Dataset BigQuery | `PROJECT_ID.counter_presencial_vnext` |
| Catálogo físico de entrada | `catalog_vnext` en ese dataset |
| Tabla y vista de salida | `counter_presencial_resultados_vnext`, `counter_presencial_segmentos_vnext` |
| Modelo remoto BigQuery | `gemini_roles_vnext` en ese dataset |
| Conexión BigQuery–Gemini | `PROJECT_ID.us.counter-presencial-vnext-gemini` |
| Cuentas de servicio | `cpv-prepare`, `cpv-gpu`, `cpv-roles`, `cpv-build`, `cpv-workflow`, `cpv-operator` |
| Workflow opcional | `counter-presencial-vnext` |
| Scheduler, Eventarc y Pub/Sub | Sin configurar: primera prueba manual |
| Cachés, manifiestos y locks | Solo dentro del bucket nuevo de salida |
| Logs y consumo de APIs | Proyecto nuevo |

El endpoint administrado de Gemini es un servicio de Google: usamos el mismo
producto, pero con modelo remoto, conexión, identidad y consumo del proyecto
nuevo. No se referencia el modelo remoto ni la conexión de Paul. Los checkpoints
públicos de NVIDIA tampoco son componentes desplegados por Paul.

Separar proyectos evita competir por las cuotas **por proyecto y región** de GPU.
El proyecto nuevo necesita su propia cuota L4 sin redundancia zonal, además de
acceso/cuota para Gemini. La disponibilidad regional, las políticas de organización
y la facturación corporativa pueden seguir siendo comunes; no se garantiza una
GPU física reservada ni aislamiento de límites de organización.

## Preparación para Paul

1. Crear un proyecto GCP dedicado con facturación y habilitar cuota L4 en
   `us-central1`. El generador rechaza el proyecto GCP anterior conocido.
2. Crear un repositorio GitHub nuevo y subir **solo el contenido de esta carpeta**
   a su raíz. Conectarlo a Cloud Build en el proyecto nuevo. No reutilizar un
   activador de otro proyecto ni apuntarlo al repositorio combinado.
3. Copiar `deployment.example.json` a `deployment.local.json`. Completar el ID del
   proyecto nuevo, poner `new_project_confirmed: true`, seleccionar commits
   inmutables de los modelos y un endpoint Gemini disponible allí. Ajustar fechas.
4. Ejecutar **solo la generación local**:

```bash
python3 scripts/render_deployment.py deployment.local.json
```

Esto produce `generated/inventory.json`, SQL y scripts revisables. No contacta
GCP, no configura credenciales y no ejecuta los comandos que escribe. El directorio
`generated/` debe no existir; así no se sobrescribe una propuesta anterior.

## Aplicación posterior, por Paul

Revisar primero el inventario y los scripts. Los comandos siguientes crean o
consumen recursos cuando Paul los ejecute: no forman parte de esta entrega aplicada.
El generador no crea ni factura proyectos automáticamente.

1. `bash generated/bootstrap.sh`: APIs, identidades nuevas, buckets, repositorio de
   imágenes, dataset, catálogo físico, resultados, conexión y modelo remoto propios.
   Usa operaciones de creación: si un recurso ya existe, se detiene en lugar de
   adoptarlo o reemplazarlo. Ante fallo parcial, revisar qué se creó antes de continuar.
2. Copiar audios completos a `gs://PROJECT_ID-cpv-input/...` y cargar metadatos en
   `PROJECT_ID.counter_presencial_vnext.catalog_vnext`. Usar los campos de
   `sql/catalog.sql.template`. **No crear una vista o tabla externa sobre recursos
   de Paul, ni conservar URIs GCS de sus buckets.** Cada fila requiere process_day;
   para audios completos basta gcs_uri, process_day y fechas de carga.
3. `bash generated/build.sh`: prueba y construye ambas imágenes. No crea Jobs ni
   ejecuta audios. Usa cuenta de compilación y bucket de staging nuevos.
4. Revisar los digests publicados y exportar `CPV_CPU_DIGEST=sha256:...` y
   `CPV_GPU_DIGEST=sha256:...`; después ejecutar `bash generated/create_jobs.sh`.
   Crea tres Jobs nuevos por digest; no ejecuta Jobs automáticamente.
5. Dar al operador humano permiso Token Creator únicamente sobre `cpv-operator`
   del proyecto nuevo para las pruebas por impersonación. Quien compile/cree
   recursos también necesita permisos de administración y actAs sobre las cuentas
   nuevas; no se incluyen llaves privadas ni se reutilizan cuentas de Paul.
6. `bash generated/run_manual.sh`: invoca prepare, obtiene el número de tareas y
   ejecuta GPU y roles directamente desde Cloud Shell, sin Workflows. Admite un ID
   de lote como primer argumento para reintentos; las etapas conservan sus cachés.
7. Opcional: `bash generated/create_trigger.sh` crea el activador propio para
   compilar el repositorio nuevo. El activador **solo construye imágenes**: actualizar
   Jobs o procesar audios requiere una acción aparte. No se crea Scheduler.
8. El Workflow independiente es opcional y se define en `workflows/`; su script
   `generated/optional_workflow.sh` exige que no existan workflows en el proyecto.
   No es necesario para el primer piloto ni elimina la necesidad de cuota GPU.

Todos los comandos generados fijan el proyecto explícitamente; no cambian el
proyecto activo global de gcloud. Los permisos BigQuery se limitan al proyecto
nuevo; dentro de él, prepare lee y roles publica. GPU no tiene permisos BigQuery.

## Pruebas y alcance

```bash
python -B -m unittest discover -s tests
```

Las pruebas se ejecutan sin servicios cloud ni modelos reales. Cubren aislamiento
contra configuración cruzada, imports independientes y sintaxis de los scripts
además de los contratos del pipeline. Ver [PIPELINE.md](PIPELINE.md) para el detalle.

Pendiente: creación de infraestructura por Paul, conexión del repositorio nuevo,
selección de checkpoints, build real del perfil candidato y validación con audio
etiquetado. La entrega no equivale a un despliegue ni a un benchmark validado.

Fuentes técnicas de configuración consultadas el 2026-11-17:
- https://docs.cloud.google.com/run/docs/configuring/jobs/gpu
- https://docs.cloud.google.com/run/quotas
- https://docs.cloud.google.com/bigquery/docs/generate-table
- https://docs.cloud.google.com/sdk/gcloud/reference/builds/triggers/create/github
- https://docs.cloud.google.com/iam/docs/roles-permissions/run
