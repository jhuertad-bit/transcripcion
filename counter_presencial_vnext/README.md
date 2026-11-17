# Counter Presencial VNext — entrega independiente

Este directorio es un proyecto ejecutable por sí solo. El destino acordado es
**`prd-utpbi-data-operation`**, con **recursos exclusivos de Counter Presencial
VNext**. No hace falta crear otro proyecto GCP. Puede alojarse en la carpeta
`counter_presencial_vnext/` del repositorio de Paul, con un activador propio que
solo compile esa carpeta. Esta entrega no ejecuta despliegues ni crea recursos.

El proyecto original de Paul permanece byte por byte como en el ZIP recibido.
Esta carpeta contiene el procesamiento Canary + Nemotron + roles contextuales con
Gemini, sus utilidades locales, pruebas, imágenes y configuración propias.
No importa ni necesita ejecutar componentes del proyecto original.

Para corregir la compilación actual de Paul, comenzar por [PARA_PAUL.md](PARA_PAUL.md).

## Recursos propios

`PROJECT_ID` es `prd-utpbi-data-operation` en el despliegue acordado.

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
| Logs y consumo de APIs | PRD; identificar los Jobs y cuentas `cpv-*` |

El endpoint administrado de Gemini es un servicio de Google: usamos el mismo
producto, pero con modelo remoto, conexión e identidad propios dentro de PRD. No se referencia el modelo remoto ni la conexión de Paul. Los checkpoints
públicos de NVIDIA tampoco son componentes desplegados por Paul.

Las cuotas **por proyecto y región** de GPU, Cloud Build y Gemini se comparten
con el resto de PRD. Separar nombres, datos y permisos evita interferencias en
los recursos existentes, pero no reserva cuota ni capacidad. Antes del piloto,
verificar cuota L4 sin redundancia zonal en la región elegida y coordinar el uso
con Paul. El perfil inicial sigue usando `us-central1`; no presupone cuota allí.

## Preparación para Paul

1. Usar el proyecto existente `prd-utpbi-data-operation` y comprobar sus cuotas y
   permisos. No ejecutar Jobs de Paul para probar nuestra implementación.
2. Subir esta carpeta actualizada al repositorio y crear un activador nuevo.
   En `jhuertad-bit/transcripcion`, el YAML es
   `counter_presencial_vnext/cloudbuild.yaml` y `_SOURCE_DIR=counter_presencial_vnext`.
   El filtro del activador debe incluir solo `counter_presencial_vnext/**`.
3. Copiar `deployment.example.json` a `deployment.local.json`. Mantener el proyecto
   PRD, revisar el inventario y poner `isolated_resources_confirmed: true` cuando
   se confirme que los recursos nombrados serán exclusivos. Elegir commits
   inmutables de los modelos y un endpoint Gemini disponible en PRD. Ajustar fechas.
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
El generador no crea proyectos GCP ni ejecuta estos scripts automáticamente.

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
   del proyecto PRD para las pruebas por impersonación. Quien compile/cree
   recursos también necesita permisos de administración y actAs sobre las cuentas
   nuevas; no se incluyen llaves privadas ni se reutilizan cuentas de Paul.
6. `bash generated/run_manual.sh`: invoca prepare, obtiene el número de tareas y
   ejecuta GPU y roles directamente desde Cloud Shell, sin Workflows. Admite un ID
   de lote como primer argumento para reintentos; las etapas conservan sus cachés.
7. Opcional: `bash generated/create_trigger.sh` crea el activador propio para
   compilar exclusivamente nuestra carpeta. El activador **solo construye imágenes**: actualizar
   Jobs o procesar audios requiere una acción aparte. No se crea Scheduler.
8. El Workflow independiente es opcional y se define en `workflows/`; su script
   `generated/optional_workflow.sh` rechaza reemplazar nuestro workflow si ya existe.
   No es necesario para el primer piloto ni elimina la necesidad de cuota GPU.

Todos los comandos generados fijan el proyecto explícitamente; no cambian el
proyecto activo global de gcloud. Los permisos de datos BigQuery se limitan al
dataset propio `counter_presencial_vnext`: prepare lee y roles publica. El permiso
de conexión se asigna solo a nuestra conexión. GPU no tiene permisos BigQuery y
los operadores reciben permisos de Cloud Run solo sobre nuestros tres Jobs.
Las cuentas nuevas que consultan BigQuery necesitan Job User en PRD y la cuenta
de nuestra conexión necesita Vertex AI User en PRD para invocar Gemini.

## Pruebas y alcance

Si esta carpeta está dentro de un repositorio con otros proyectos, el activador
debe leer `counter_presencial_vnext/cloudbuild.yaml` y fijar la sustitución
`_SOURCE_DIR=counter_presencial_vnext`. Los cuatro pasos usan ese directorio,
incluidos ambos contextos Docker; los cambios de otros proyectos deben excluirse
con el filtro de archivos `counter_presencial_vnext/**` del activador propio.
Elegir el YAML dentro de una carpeta no cambia automáticamente el directorio de
ejecución de Cloud Build. Sin esta configuración, el primer import falla con
`ModuleNotFoundError: No module named 'counter_presencial'`.

Cuando el contenido del proyecto está en la raíz de su repositorio, o se envía
solo esta carpeta con `generated/build.sh`, se mantiene `_SOURCE_DIR=.` (default).
No hace falta instalar dependencias para resolver este error de rutas.

```bash
python -B -m unittest discover -s tests
```

Las pruebas se ejecutan sin servicios cloud ni modelos reales. Cubren aislamiento
contra configuración cruzada, imports independientes y sintaxis de los scripts
además de los contratos del pipeline. Ver [PIPELINE.md](PIPELINE.md) para el detalle.

Pendiente: revisar el build, crear recursos propios en PRD y conectar su activador,
selección de checkpoints, build real del perfil candidato y validación con audio
etiquetado. La entrega no equivale a un despliegue ni a un benchmark validado.

Fuentes técnicas de configuración consultadas el 2026-11-17:
- https://docs.cloud.google.com/run/docs/configuring/jobs/gpu
- https://docs.cloud.google.com/run/quotas
- https://docs.cloud.google.com/bigquery/docs/generate-table
- https://docs.cloud.google.com/sdk/gcloud/reference/builds/triggers/create/github
- https://docs.cloud.google.com/iam/docs/roles-permissions/run
