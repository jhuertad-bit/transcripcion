-- Ejecutar una sola vez. No reemplaza ni modifica objetos existentes.
-- Usar la misma ubicación BigQuery del catálogo.
CREATE TABLE `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_entrada` AS
SELECT a.*,
  b.database AS sede,
  NULLIF(TRIM(b.asesorcodigo), '') AS asesor_codigo,
  NULLIF(TRIM(b.asesorusuario), '') AS asesor_usuario,
  NULLIF(TRIM(b.asesornombre), '') AS asesor_nombre,
  NULLIF(TRIM(CONCAT(COALESCE(b.clienteprimernombre, ''), ' ',
    COALESCE(b.clienteapellidopaterno, ''))), '') AS cliente_nombre,
  b.userid AS qs_usuario_id,
  b.rowidkey AS ticket_rowidkey,
  b.windowid AS ventanilla_id,
  b.starttimestamp AS atencion_inicio,
  b.endtimestamp AS atencion_fin,
  b.transferido AS ticket_transferido,
  b.process_datatime AS ticket_carga
FROM `prd-utpbi-data-operation.raw_queue_smart.queuesmart_mp3_catalog` a
JOIN `prd-utpbi-data-operation.raw_queue_smart.tickets_hist_raw` b
  ON a.source_file_name = b.audio
WHERE b.audio LIKE '%202611%'
  AND a.process_day BETWEEN '2026-11-02' AND '2026-11-07'
  AND b.database IN ('QSChiclayo', 'QSChimbote', 'QSTrujillo');

ASSERT (SELECT COUNT(*) = COUNT(DISTINCT gcs_uri)
  FROM `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_entrada`)
  AS 'Entrada duplicada o gcs_uri nulo: revisar antes de ejecutar';

CREATE TABLE `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_resultados` (
  processing_key STRING NOT NULL, recording_id STRING, audio_uri STRING,
  audio_generation STRING, result_uri STRING, pipeline_version STRING,
  duration_seconds FLOAT64, elapsed_seconds FLOAT64, processed_at TIMESTAMP,
  serialized_level_1 STRING, serialized_level_2 JSON
)
PARTITION BY DATE(processed_at)
CLUSTER BY recording_id, processing_key;

CREATE VIEW `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_consulta` AS
SELECT r.*,
  SAFE_CAST(JSON_VALUE(serialized_level_2, '$.catalog.process_day') AS DATE) AS process_day,
  JSON_VALUE(serialized_level_2, '$.catalog.sede') AS sede,
  JSON_VALUE(serialized_level_2, '$.catalog.asesor_codigo') AS asesor_codigo,
  JSON_VALUE(serialized_level_2, '$.catalog.asesor_usuario') AS asesor_usuario,
  JSON_VALUE(serialized_level_2, '$.catalog.asesor_nombre') AS asesor_nombre,
  JSON_VALUE(serialized_level_2, '$.catalog.cliente_nombre') AS cliente_nombre,
  JSON_VALUE(serialized_level_2, '$.catalog.ticket_rowidkey') AS ticket_rowidkey
FROM `prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_resultados` r;
