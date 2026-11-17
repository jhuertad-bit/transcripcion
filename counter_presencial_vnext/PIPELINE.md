# Counter Presencial VNext — pipeline independiente

## Procesamiento

1. **Prepare (CPU):** consulta exclusivamente el catálogo propio, agrupa audios por
   conversación, comprueba integridad y fija generaciones de objetos. Publica un
   manifiesto y sus particiones en el bucket propio de salida.
2. **Acoustic (L4):** descarga copias de los audios del bucket propio. Normaliza una
   vez a mono/16 kHz, mantiene modelos cargados por tarea y ejecuta Nemotron para
   intervalos acústicos y Canary para texto y timestamps nativos de NeMo/CTC.
   Procesa ventanas de 24 s con contexto; cada palabra pertenece a una ventana por
   su punto medio. Conserva todos los canales acústicos; marca más de cuatro voces.
3. **Roles (CPU):** oculta datos personales localmente y entrega a Gemini toda la
   conversación en texto. Cada solicitud clasifica un conjunto de turnos, siempre
   con contexto global. El prompt español adapta las reglas de SAXO V3.1:
   representación institucional frente a necesidad personal, acompañantes como
   CLIENTE, sin alternancia forzada ni asignación por orden o número de voz.
4. **Fusión:** conserva el rol inicial y contrasta evidencia acústica independiente.
   Usa anclas originales, cobertura, pureza, consenso y exclusión del propio turno.
   Una segunda revisión contextual tiene presupuesto limitado y caché. Mantiene
   INCIERTO cuando la evidencia no alcanza.
5. **Publicación:** JSON, TXT, serialización y MERGE idempotente en la tabla propia.
   Cada etapa y respuesta validada tienen caché. Reintentar roles no repite GPU.

Gemini asigna roles por intervención; no recibe audio en este diseño ni crea
identidades físicas de voz. Los speakers acústicos provienen de Nemotron. El prompt
no contiene esos speakers en la primera pasada. Los roles permitidos son
ASESOR/CLIENTE/INCIERTO y requieren evidencia referenciada por turn_id. No se usan
porcentajes de confianza inventados por el modelo como probabilidades calibradas.

## Integridad de la conversación

Preferir un audio original completo copiado al bucket nuevo. Los fragmentos
necesitan índices contiguos, offsets reales y recording_complete=true. Se valida
continuidad a una muestra tras decodificar; no se cierran huecos artificialmente.
Sin metadatos suficientes, la conversación queda en needs_source_review.
El mapa opcional PRESENCIAL_ORIGINALS_MAP_URI también debe estar en el bucket de
entrada propio. Las referencias del catálogo deben apuntar a las copias nuevas.

No se inventan timestamps si falla la correspondencia entre texto y palabras
alineadas: se conserva texto y se marca revisión. Un problema de tiempos no impide
por sí solo clasificar el contenido textual; texto sospechoso sí exige revisión.

## Ejecución

Desde esta carpeta independiente, después de configurar sus recursos propios:

```bash
python -m counter_presencial.main prepare
python -m counter_presencial.main acoustic
python -m counter_presencial.main roles
```

Las etapas acústicas requieren GPU CUDA. Los contenedores CPU no instalan Torch.
El proceso principal valida los nombres de recursos antes de crear clientes GCP.
No importa worker, counter_common ni otro módulo del proyecto original.

## Límites iniciales

- Máximo 100 conversaciones por lote, tres por tarea, GPU con paralelismo 1.
- Máximo 7.200 s y 500 MiB comprimidos por conversación.
- Hasta 180.000 caracteres por prompt; no se trunca silenciosamente.
- Hasta 40 objetivos por llamada, 64 intentos lógicos de primera pasada y ocho
  revisiones por conversación; estados transitorios de Gemini tienen reintento acotado.
- Revisiones y presupuestos se configuran con las variables PRESENCIAL_* del código.
- ASR: Canary; diarización: Nemotron o none para diagnóstico explícito.

## Estado de validación

Pruebas offline de contratos, prompts, evidencias, fusión, caché, reanudación,
leases e independencia de recursos. No se ha construido ni ejecutado la imagen
candidata en L4, llamado a Gemini ni medido calidad/costo con los audios reales.
Los pins de checkpoints deben seleccionarse antes del piloto. El checkpoint y la
versión NeMo deben exponer timestamps nativos; si no, el proceso falla explícitamente.

Antes de promover: construir ambas imágenes, comprobar imports/VRAM, ejecutar
3–5 conversaciones etiquetadas, revisar omisiones, roles, solapamientos, precisión
de timestamps y ocultamiento de datos; medir latencia y costo total por hora de
audio. No se garantiza anonimización exhaustiva con Presidio y regex.
