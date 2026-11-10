CREATE TABLE IF NOT EXISTS
`prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_intentos`
(
  attempt_id STRING NOT NULL,
  processing_key STRING NOT NULL,
  recording_id STRING NOT NULL,
  tipo_origen STRING NOT NULL,
  fecha_audio DATE NOT NULL,
  gcs_uri STRING,
  audio_generation STRING,
  estado STRING NOT NULL,
  etapa STRING,
  pipeline_version STRING,
  execution_id STRING,
  created_at TIMESTAMP NOT NULL,
  started_at TIMESTAMP,
  updated_at TIMESTAMP,
  finished_at TIMESTAMP,
  elapsed_seconds FLOAT64,
  result_uri STRING,
  error_type STRING,
  error_message STRING
)
PARTITION BY DATE(created_at)
CLUSTER BY tipo_origen, estado, processing_key;
