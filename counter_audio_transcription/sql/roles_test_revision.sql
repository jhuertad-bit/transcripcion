-- Consultas de lectura para después de ejecutar el piloto.
SELECT recording_id, audio_uri, model, prompt_version, status, roles, error_type, updated_at
FROM `prd-utpbi-data-operation.raw_queue_smart.counter_roles_test`
ORDER BY updated_at DESC;

SELECT recording_id, audio_uri, start, `end`, speaker_id, role, requires_review, text
FROM `prd-utpbi-data-operation.raw_queue_smart.counter_roles_test_segmentos`
WHERE model = 'gemini-3.5-flash' AND prompt_version = 'counter-roles-v2'
ORDER BY audio_uri, start;

-- Contrastar con la revisión manual del usuario SOLO para este archivo.
SELECT JSON_VALUE(r, '$.speaker_id') AS speaker_id,
  JSON_VALUE(r, '$.role') AS role_propuesto,
  CASE JSON_VALUE(r, '$.speaker_id')
    WHEN 'SPEAKER_01' THEN 'Asesor' WHEN 'SPEAKER_00' THEN 'Cliente'
  END AS role_manual,
  JSON_VALUE(r, '$.requires_review') AS requires_review
FROM `prd-utpbi-data-operation.raw_queue_smart.counter_roles_test`,
UNNEST(JSON_QUERY_ARRAY(roles)) r
WHERE ENDS_WITH(audio_uri, '/015AG3-20261031-124707.flac')
  AND model = 'gemini-3.5-flash' AND prompt_version = 'counter-roles-v2'
  AND status = 'completed';
