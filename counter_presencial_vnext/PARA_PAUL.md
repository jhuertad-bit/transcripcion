# Entrega para Paul — workflow original, recursos propios

## Qué hacer con esta entrega

1. Descomprimir y reemplazar **solo** `counter_presencial_vnext/` en el repo.
   Incluir todos los archivos nuevos: hay dos entornos de modelos y sus locks.
2. Mantener nuestro activador existente apuntando a
   `counter_presencial_vnext/cloudbuild.yaml`.
3. Mantener `_SOURCE_DIR=counter_presencial_vnext` y `_REGION=us-central1`.
4. Hacer push y comprobar un **build nuevo con el commit nuevo**.

No basta con cambiar la línea de NeMo de la entrega anterior. Llegan juntos
los Dockerfiles, requirements/constraints, el ejecutor separado de Canary,
las funciones de SAXO y los ajustes de orquestación.

## Qué cambió

Se conserva el esquema de Paul: **un Job con prepare, worker y roles**.
Nuestro Job es `counter-presencial-vnext-gpu`; no se ejecuta el suyo.
Las dos imágenes se compilan, pero no hacen falta tres Jobs para el piloto.

- Nemotron utiliza el Transformers fijado por SAXO.
- Canary utiliza NeMo 2.7.3 en otro entorno Python dentro de la misma imagen.
- Solo un modelo GPU está residente a la vez.
- Se aplican ventanas de voz, limpieza de bucles y control multihablante de SAXO.
- Hay checkpoints de diarización y por ventana para reintentos.
- Gemini conserva el diálogo completo, fusión y segunda revisión.

La atribución contextual sigue el contrato de las ramas de SAXO: intervenciones
formadas con palabras, pausas y puntuación; Gemini sustituye a JEV para atribuir
cada una usando el diálogo completo. No se impone un rol fijo por voz. Las
funciones de segmentación se copiaron del fuente y se compararon directamente.

## Después del build

El activador **solo compila**. Antes de probar hay que revisar o crear nuestros
recursos, configurar Gemini y completar los commits de los pesos en
`deployment.local.json`. El README contiene el orden y el generador.

Si ya existe una carpeta `generated/` anterior, conservarla aparte antes de
generar la nueva: los scripts anteriores contemplaban tres Jobs. Esta entrega
genera **uno** con la cuenta nueva `cpv-pipeline`; no elimina recursos anteriores.

El perfil inicial usa 1 L4, 8 CPU, 32 GiB, paralelismo 1 y un máximo de tres
conversaciones por lote. Requiere cuota disponible y los permisos propios.
La configuración de CPU/memoria respeta la tabla de Cloud Run Jobs:
https://docs.cloud.google.com/run/docs/configuring/jobs/cpu

Después de crear el Job e introducir audios y catálogo propios:

```bash
bash generated/run_manual.sh
```

Usa el mismo contrato JOB_ROLE de su workflow. Si una etapa falla, el comando
devuelve error. Para reintentar el lote, pasar su ID; para repetir únicamente
roles, usar `--roles-only ID_DEL_LOTE`.

## Qué está validado y qué falta

Las pruebas offline, instalaciones limpias y los imports se verifican antes
de entregar. Los checks de imagen no descargan pesos ni llaman a Gemini.
Falta confirmar la compilación Docker en GCP y el primer audio en L4; no hay
una garantía de cero errores de infraestructura, permisos o calidad.
