-- Última versión publicada por audio (un reproceso con otro modelo conserva el histórico).
SELECT tipo_origen, fecha_audio, file_name, folder_uuid, gcs_uri,
       duration_seconds, processed_at, serialized_level_1, result_uri
FROM `prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_transcripciones`
WHERE fecha_audio = DATE '2026-11-09'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY tipo_origen, recording_id ORDER BY processed_at DESC, processing_key DESC
) = 1;

-- Historial de intentos, incluidos errores.
SELECT tipo_origen, estado, etapa, COUNT(*) AS intentos
FROM `prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_intentos`
WHERE fecha_audio = DATE '2026-11-09'
GROUP BY 1, 2, 3;

-- Segmentos con tiempos y hablantes; speaker_id vale solo dentro de cada audio.
SELECT t.tipo_origen, t.file_name, t.processing_key, orden,
       SAFE_CAST(JSON_VALUE(s, '$.start') AS FLOAT64) AS inicio_segundos,
       SAFE_CAST(JSON_VALUE(s, '$.end') AS FLOAT64) AS fin_segundos,
       JSON_VALUE(s, '$.speaker_id') AS speaker_id,
       JSON_VALUE(s, '$.text') AS texto,
       JSON_VALUE(s, '$.assignment_status') AS estado_asignacion
FROM `prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_transcripciones` t
CROSS JOIN UNNEST(JSON_QUERY_ARRAY(serialized_level_2, '$.transcription')) s WITH OFFSET orden
WHERE fecha_audio = DATE '2026-11-09'
ORDER BY tipo_origen, file_name, processing_key, orden;
