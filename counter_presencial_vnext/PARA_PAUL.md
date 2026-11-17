# Primera compilación en PRD

Destino: `prd-utpbi-data-operation`. Mantener todos nuestros recursos con los
nombres exclusivos del README. Las cuotas del proyecto sí se comparten.

## 1. Actualizar el código

Reemplazar solo `counter_presencial_vnext/` con esta entrega. Incluye dos
correcciones que deben llegar juntas:

- `cloudbuild.yaml`: los cuatro pasos usan `dir: '${_SOURCE_DIR}'`.
- `counter_presencial/isolation.py` y configuración: permiten PRD y siguen
  rechazando buckets, tablas y modelos fuera de nuestro espacio de recursos.

No cambiar las otras carpetas del repositorio. El YAML corregido por sí solo
resuelve el import, pero la versión anterior del código rechazaba PRD después.

## 2. Revisar únicamente nuestro activador

Se puede conservar el nombre existente `deploy-counter-presencial-vnext`.
No hace falta crear un segundo activador si ese ya es exclusivo de esta carpeta.

| Ajuste | Valor |
|---|---|
| Repositorio | `jhuertad-bit/transcripcion` |
| Rama | `main` |
| Archivo de configuración | `counter_presencial_vnext/cloudbuild.yaml` |
| Filtro de archivos incluidos | `counter_presencial_vnext/**` |
| Sustitución `_SOURCE_DIR` | `counter_presencial_vnext` |
| Sustitución `_REGION` | `us-central1` |
| Cuenta de compilación propuesta | `cpv-build@prd-utpbi-data-operation.iam.gserviceaccount.com` |

La cuenta de compilación se selecciona en la configuración del activador: no es
una sustitución `_SERVICE_ACCOUNT`. Debe existir y tener los permisos descritos
en el README. También debe existir nuestro repositorio Docker de Artifact Registry
`counter-presencial-vnext` en `us-central1`, con acceso de escritura para esa cuenta.
El código no los crea durante el build.

`$PROJECT_ID` y `$BUILD_ID` los proporciona Cloud Build. Este YAML no lee
`_LOCATION`, `_REPOSITORY`, `_BQ_LOCATION`, `_JOB_NAME`, `_SERVICE_ACCOUNT`,
`_FIRESTORE_SECRET`, `_ENABLE_SCHEDULER`, `_SCHEDULE`, `_SCHEDULER_NAME`,
`_TIME_ZONE`, `_WORKFLOW_NAME` ni `_WORKFLOW_SERVICE_ACCOUNT`.
No copiar nombres o cuentas de otros procesos. No modificar sus activadores.

Guardar la configuración y ejecutar un build sobre el commit nuevo de `main`.
Reintentar el commit antiguo `1faf1c6` no incorpora los archivos corregidos.
Comprobar un ID de build nuevo; `fa42f474-6784-4cde-a967-073fda99ef73` es el fallo
anterior. La selección del YAML no cambia por sí sola el directorio de trabajo.

## 3. Después de compilar

Este activador construye y publica dos imágenes. No crea ni ejecuta los Jobs.
Seguir el README para preparar los recursos propios y crear:

- `counter-presencial-vnext-prepare`
- `counter-presencial-vnext-gpu`
- `counter-presencial-vnext-roles`

La configuración de ejecución está en `deployment.local.json` y los archivos
que genera `scripts/render_deployment.py`; no se añade al activador. Antes de
crearlos hay que completar las revisiones inmutables de Canary y Nemotron,
el endpoint Gemini habilitado en PRD, y las fechas de la prueba. Confirmar cuota
L4 en `us-central1`, preparar las cuentas/permisos nuevos y copiar algunos audios
completos y sus metadatos al bucket y catálogo propios.

No ejecutar `bootstrap.sh` a ciegas si ya se crearon recursos: se detiene cuando
un nombre existe. Revisar lo existente antes de continuar. La primera prueba
se lanza con `bash generated/run_manual.sh`, una vez creados los tres Jobs.

## Validación de esta entrega

Pruebas offline y comprobación local de los pasos Python y rutas Docker.
Esto no confirma todavía un build Docker en GCP ni ejecución de modelos/Gemini.
