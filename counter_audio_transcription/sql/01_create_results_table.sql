-- Plantilla MANUAL. Sustituir YOUR_PROJECT_ID. No ejecutada por el código.
-- Crear primero raw_counter_audios en la ubicación que se acuerde.
CREATE TABLE IF NOT EXISTS `YOUR_PROJECT_ID.raw_counter_audios.counter_audios_transcripcion` (
  processing_key STRING NOT NULL,
  recording_id STRING,
  audio_uri STRING,
  audio_generation STRING,
  result_uri STRING,
  pipeline_version STRING,
  duration_seconds FLOAT64,
  elapsed_seconds FLOAT64,
  processed_at TIMESTAMP,
  serialized_level_1 STRING,
  serialized_level_2 JSON
)
PARTITION BY DATE(processed_at)
CLUSTER BY recording_id, processing_key;
