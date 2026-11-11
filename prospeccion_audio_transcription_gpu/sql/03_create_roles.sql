-- Reservada para la etapa posterior de clasificación semántica.
-- La diarización actual genera speaker_id, no roles de negocio.
CREATE TABLE IF NOT EXISTS
`prd-utpbi-data-operation.raw_transcripciones.hist_prospeccion_audio_roles_gpu`
(
  processing_key STRING NOT NULL,
  recording_id STRING NOT NULL,
  tipo_origen STRING NOT NULL,
  fecha_audio DATE NOT NULL,
  modelo_roles STRING NOT NULL,
  version_prompt STRING NOT NULL,
  estado STRING NOT NULL,
  roles JSON,
  requiere_revision BOOL,
  motivo_revision STRING,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP,
  elapsed_seconds FLOAT64,
  error_type STRING,
  error_message STRING
)
PARTITION BY fecha_audio
CLUSTER BY tipo_origen, recording_id, processing_key;
