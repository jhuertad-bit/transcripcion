CREATE TABLE IF NOT EXISTS
  `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control`
(
  uri STRING,
  file_name STRING,
  gcs_path STRING,
  source_gcs_uri STRING,
  source_gcs_path STRING,
  folder_uuid STRING,
  audio_id STRING,
  firestore_path STRING,
  mentor_id STRING,
  mentor_nombre STRING,
  mentor_email STRING,
  mentor_campus STRING,
  correo_grabador STRING,
  activityid STRING,
  colegio STRING,
  codigo_modular STRING,
  campus_evento STRING,
  fecha_evento TIMESTAMP,
  tipo_charla_mentor STRING,
  tipos_charla_mentor_json STRING,
  prompt_name STRING,
  prompt_sha256 STRING,
  estado_cruce STRING,
  fecha_audio DATE,
  fecha_procesamiento TIMESTAMP,
  file_size_bytes INT64,
  content_type STRING,
  duration_seconds FLOAT64,
  audio_codec STRING,
  sample_rate_hz INT64,
  channels INT64,
  format_name STRING,
  bit_rate INT64,
  sync_mode STRING,
  copy_result STRING,

  estado_proceso STRING,
  motivo_estado STRING,
  json_error_detalle STRING,
  fecha_evaluacion TIMESTAMP,

  fecha_registro TIMESTAMP,
  fecha_actualizacion TIMESTAMP
)
PARTITION BY fecha_audio
CLUSTER BY estado_proceso, folder_uuid;

CREATE TABLE IF NOT EXISTS
  `prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd`
(
  process_date DATE,
  uri STRING,
  audio STRING,
  gcs_path STRING,
  source_gcs_uri STRING,
  folder_uuid STRING,
  audio_id STRING,
  firestore_path STRING,
  mentor_id STRING,
  mentor_nombre STRING,
  mentor_email STRING,
  mentor_campus STRING,
  correo_grabador STRING,
  activityid STRING,
  colegio STRING,
  codigo_modular STRING,
  campus_evento STRING,
  fecha_evento TIMESTAMP,
  tipo_charla_mentor STRING,
  tipos_charla_mentor_json STRING,
  prompt_name STRING,
  prompt_sha256 STRING,
  estado_cruce STRING,
  fecha_audio DATE,
  duracion FLOAT64,
  json_text STRING,
  final_json JSON,
  full_response JSON,
  status STRING,

  puntaje_total FLOAT64,
  resumen_general STRING,

  b1_comunicacion_oral_resultado STRING,
  b1_claridad_discurso_calif STRING,
  b1_claridad_discurso_motivo STRING,
  b1_estructura_mensaje_calif STRING,
  b1_estructura_mensaje_motivo STRING,
  b1_palabras_clave_calif STRING,
  b1_palabras_clave_motivo STRING,

  b1_generacion_impacto_resultado STRING,
  b1_mensajes_motivacionales_calif STRING,
  b1_mensajes_motivacionales_motivo STRING,
  b1_cierre_estructurado_calif STRING,
  b1_cierre_estructurado_motivo STRING,

  b1_conexion_engagement_resultado STRING,
  b1_empatia_cercania_calif STRING,
  b1_empatia_cercania_motivo STRING,
  b1_interaccion_activacion_publico_calif STRING,
  b1_interaccion_activacion_publico_motivo STRING,

  b2_conocimiento_institucional_resultado STRING,
  b2_precision_informacion_calif STRING,
  b2_precision_informacion_motivo STRING,
  b2_cobertura_temas_calif STRING,
  b2_cobertura_temas_motivo STRING,

  b2_dominio_narrativa_resultado STRING,
  b2_storytelling_calif STRING,
  b2_storytelling_motivo STRING,

  b2_liderazgo_resultado STRING,
  b2_cumplimiento_duracion_estructura_calif STRING,
  b2_cumplimiento_duracion_estructura_motivo STRING,
  b2_manejo_interrupciones_calif STRING,
  b2_manejo_interrupciones_motivo STRING,

  b3_orientacion_servicio_resultado STRING,
  b3_calidad_respuestas_calif STRING,
  b3_calidad_respuestas_motivo STRING,
  b3_seguridad_responder_calif STRING,
  b3_seguridad_responder_motivo STRING,
  b3_refuerzo_institucional_calif STRING,
  b3_refuerzo_institucional_motivo STRING,

  load_date DATETIME
)
PARTITION BY process_date
CLUSTER BY audio, uri;


CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_evaluaciones`
PARTITION BY DATE(evaluated_at) CLUSTER BY uri AS
SELECT CAST(NULL AS STRING) AS run_id, CURRENT_TIMESTAMP() AS evaluated_at, f.*
FROM `prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd` f WHERE FALSE;
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_control_ejecucion`
PARTITION BY DATE(recorded_at) CLUSTER BY uri AS
SELECT CAST(NULL AS STRING) AS run_id, CURRENT_TIMESTAMP() AS recorded_at, c.*
FROM `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` c WHERE FALSE;
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.mentores_pipeline_lock`
(lock_id STRING, owner STRING, expires_at TIMESTAMP);
MERGE `prd-utpbi-data-operation.raw_cita_promotor.mentores_pipeline_lock` t
USING (SELECT 'pipeline' AS lock_id) s ON t.lock_id = s.lock_id
WHEN NOT MATCHED THEN INSERT (lock_id) VALUES (s.lock_id);
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_pipeline_runs`
(run_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP, status STRING,
 date_from DATE, date_to DATE, reprocess BOOL, error_detail STRING)
PARTITION BY DATE(started_at);
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_catalog_snapshots`
(snapshot_id STRING, published_at TIMESTAMP)
PARTITION BY DATE(published_at);
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_ia_respuestas`
(run_id STRING, evaluated_at TIMESTAMP, uri STRING, full_response JSON,
 clean_json_text STRING, final_json JSON, json_valido BOOL, json_error_tipo STRING)
PARTITION BY DATE(evaluated_at) CLUSTER BY uri;
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_prompt_versions`
(prompt_name STRING, prompt_sha256 STRING, prompt_text STRING, registered_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_execution_plan`
(execution_id STRING, process_date DATE, snapshot_id STRING, created_at TIMESTAMP)
PARTITION BY process_date CLUSTER BY execution_id;
