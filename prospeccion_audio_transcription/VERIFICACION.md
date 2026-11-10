# Verificación local

Fecha: 2026-11-10. Se reutilizó el núcleo de inferencia/alineación de Counter y se adaptaron fuentes, identidades, destinos y control BigQuery.

Resultado: 41 pruebas aprobadas en Python 3.14.3, análisis sintáctico de todos los archivos Python y parseo correcto de todos los YAML. Cloud Build repetirá las pruebas en Python 3.11, que es la versión de la imagen. No se dispone de Docker ni Bash localmente para validar el build o ejecutar el script de despliegue.

Ejecutar desde esta carpeta:

```bash
python -m unittest discover -s tests -v
```

Se comprobaron con dobles de prueba:

- Selección de los catálogos correctos, fechas obligatorias y deduplicación con las columnas reales.
- Omisión de completados antes de aplicar el límite, evitando que los primeros archivos bloqueen el avance.
- Separación de identidad/ruta por promotor y mentor y conservación de metadatos.
- Distribución de archivos sin duplicarlos entre tareas y continuidad tras errores individuales.
- Validación de generación y ausencia de inferencia al cambiar el objeto.
- Estados del intento, errores de descarga/modelos, IDs distintos por reintento y clave estable.
- MERGE parametrizado, tipos y compatibilidad de los campos de resultados con el DDL.
- Asociación temporal, ambigüedades, habla simultánea, reutilización de Whisper y métricas de diarización.
- Preservación del resultado previo ante fallos y publicación solo después del MERGE de transcripción.

Pendiente en GCP: build de imagen, resolución real de dependencias/pesos, permisos, validación del Workflow por el servicio, lectura de los catálogos, MERGE en BigQuery e inferencia con audios reales. Las pruebas locales no miden calidad de transcripción ni diarización ni consumo de recursos. No se ejecutaron los DDL contra BigQuery.

Actualización: las pruebas verifican la configuración reducida del activador, los parámetros del Job y la validación previa al despliegue.
