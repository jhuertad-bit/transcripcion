CREATE TABLE IF NOT EXISTS
`prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_transcripciones_gpu`
(
  tipo_origen STRING NOT NULL,
  recording_id STRING NOT NULL,
  processing_key STRING NOT NULL,
  fecha_audio DATE NOT NULL,
  catalog_table STRING,
  file_name STRING,
  folder_uuid STRING,
  gcs_uri STRING,
  audio_generation STRING,
  source_gcs_uri STRING,
  duration_seconds FLOAT64,
  fecha_procesamiento_catalogo TIMESTAMP,
  catalog_metadata JSON,
  pipeline_version STRING,
  modelo_transcripcion STRING,
  modelo_diarizacion STRING,
  idioma STRING,
  serialized_level_1 STRING,
  serialized_level_2 JSON,
  result_uri STRING,
  processing_started_at TIMESTAMP,
  processed_at TIMESTAMP,
  elapsed_seconds FLOAT64
)
PARTITION BY fecha_audio
CLUSTER BY tipo_origen, recording_id, processing_key;
