# Counter Presencial — flujo de ejecución

## Contrato con la orquestación de Paul

Se conserva un único Job y los modos `JOB_ROLE=prepare|worker|roles`.
El manifiesto y su archivo `.meta.json` contienen count/task_count. Workflows
o Cloud Shell llaman las etapas en orden, y roles_only reutiliza el manifiesto.
Los nombres y destinos pertenecen exclusivamente a Counter Presencial VNext.

## Prepare

Consulta el catálogo propio por process_day, agrupa la conversación completa,
verifica fuentes y fija generaciones GCS. Los fragmentos necesitan continuidad
demostrable. Las conversaciones sin ese dato quedan en needs_source_review.
Los lotes omiten las ya publicadas antes de aplicar el límite.

## Worker GPU

1. Normalización única por conversación a mono/16 kHz.
2. Nemotron en el runtime Transformers de SAXO. Inspección de todos los canales
   y selección de hasta cuatro significativos con el mismo umbral combinado.
   Un quinto significativo falla y queda registrado; no se oculta.
3. Guardar el timeline acústico. Liberar Nemotron antes de cargar Canary.
4. Formar ventanas con la unión de intervalos de voz, padding 0.20 s, unión de
   huecos hasta 0.55 s y máximo 24 s. Esto es un proxy acústico, no VAD de referencia.
5. Canary 1B v2 en NeMo 2.7.3 dentro de un subproceso Python separado,
   español→español con timestamps=True y CTC auxiliar. No se ejecuta WhisperX.
6. Limpiar bucles extremos preservando el texto válido, texto bruto y auditoría.
   Tiempos inexistentes, inválidos o incompatibles con el texto se conservan
   como no alineados. No se estiman a partir del borde de la ventana.
7. Crear candidatos con la función de SAXO por palabras, pausas y puntuación,
   sin exigir speaker_id. Conservar cada palabra y registrar el motivo del
   corte. Guardar JSON, procedencia, versiones y contadores de calidad.

Los checkpoints guardan el timeline y cada ventana terminada dentro de la
clave acústica. No se publica completed.json hasta completar la conversación.
Se verifican la integridad y las generaciones al recuperar una etapa.
El proceso Canary tiene un timeout acotado y se termina si el protocolo falla.
Los logs de librerías van a un archivo temporal privado, no al canal JSONL.

## Roles

Esta etapa incluye la atribución contextual por intervención que realiza JEV
en SAXO, ahora con Gemini. No impone un rol fijo por voz ni agrupa distintas
personas porque compartan el rol CLIENTE. Como en esas ramas, el modelo no
reescribe palabras, tiempos o límites de intervención. Los bloques mixtos
que no se puedan atribuir con fundamento quedan para revisión.

Ocultamiento local de PII, Gemini con toda la conversación para cada conjunto
de objetivos, validación de IDs/evidencias y conservación del rol inicial.
El prompt adapta V3.1 para orientación universitaria, contacto, seguimiento,
familiares y disponibilidad; no fuerza alternancia ni roles por palabras sueltas.
Los porcentajes de JEV no se copian como probabilidades de Gemini.

Fusión con el timeline de Nemotron: cobertura, pureza, solapamiento y anclajes
originales independientes. Los roles inferidos no se reutilizan como anclajes.
Los conflictos se ponen en cuarentena y una segunda pasada limitada puede
revisarlos con el diálogo y ejemplos acústicos. Si no alcanza la evidencia,
se mantiene INCIERTO. role_initial y role_final permanecen separados.

Publicación JSON/TXT y MERGE idempotente sobre la tabla propia. Un reintento de
publicación no repite GPU ni llamadas Gemini ya guardadas. Una conversación
sin actividad acústica significativa guarda el descarte y no llama a los modelos
de texto. El resultado no se presenta como precisión validada.

## Operación

Piloto: una conversación por tarea, máximo tres por lote, paralelismo 1.
El Job único tiene GPU asignada también en los modos CPU, como compromiso de
mantener el esquema operativo. No se crea Scheduler ni se ejecuta automáticamente
al compilar. El Workflow propio es opcional para las primeras pruebas.

La configuración rechaza recursos fuera de nuestro namespace. Las cuotas por
proyecto/región siguen compartidas en PRD. Nada de esta carpeta importa ni
modifica módulos del proyecto original.
