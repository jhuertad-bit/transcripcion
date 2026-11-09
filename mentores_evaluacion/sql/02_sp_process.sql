-- Desplegado por Cloud Build durante init. No evalua audios al crear el SP.
-- El Job llama al SP despues de publicar catalogos y adquirir el bloqueo.
CREATE OR REPLACE PROCEDURE `prd-utpbi-data-operation.raw_cita_promotor.sp_process_speech_analytics_mentores`(
  IN p_date_from DATE,
  IN p_date_to DATE,
  IN p_reprocess BOOL,
  IN p_run_id STRING,
  IN p_lock_owner STRING,
  IN p_catalog_load_id STRING
)
OPTIONS(strict_mode=FALSE, description='Speech Analytics Mentores: control, evaluacion IA, historico y publicacion final. Invocar mediante el Workflow.')
BEGIN
DECLARE v_fecha_proceso DATE DEFAULT p_date_to;
-- Configurar con los prompt_name existentes en sys_prompts.
DECLARE v_prompt_laboratorio STRING DEFAULT 'prospeccion_mentores_laboratorio';
DECLARE v_prompt_auditorio STRING DEFAULT 'prospeccion_mentores_auditorio';
DECLARE v_reprocesar_fecha BOOL DEFAULT p_reprocess;

DECLARE v_uris STRING;
DECLARE v_sql STRING;
DECLARE v_cantidad_audios_control INT64 DEFAULT 0;
DECLARE v_cantidad_audios_gemini INT64 DEFAULT 0;
DECLARE v_cantidad_resultados INT64 DEFAULT 0;
DECLARE v_cantidad_respuestas_error INT64 DEFAULT 0;

ASSERT p_date_from IS NOT NULL AND p_date_to IS NOT NULL
  AND p_date_from = p_date_to AND p_reprocess IS NOT NULL AS 'PARAMETROS_INVALIDOS_UN_DIA_POR_LLAMADA';
ASSERT REGEXP_CONTAINS(p_run_id, r'^[a-f0-9]{32}$') AS 'RUN_ID_INVALIDO';
ASSERT (SELECT COUNT(*) = 1 FROM `prd-utpbi-data-operation.raw_cita_promotor.mentores_pipeline_lock`
  WHERE lock_id='pipeline' AND owner=p_lock_owner
    AND STARTS_WITH(owner, CONCAT(p_run_id, '_')) AND expires_at>CURRENT_TIMESTAMP())
  AS 'EJECUTAR_DESDE_WORKFLOW_CON_BLOQUEO';

ASSERT (SELECT COUNT(*)>0 AND COUNT(DISTINCT catalog_load_id)=1
  AND MIN(catalog_load_id)=p_catalog_load_id FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_audios_mentores_firestore`)
  AS 'CATALOGO_CAMBIO_REINICIAR_WORKFLOW';
ASSERT (SELECT COUNT(*)>0 AND COUNT(DISTINCT catalog_load_id)=1
  AND MIN(catalog_load_id)=p_catalog_load_id FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_mentores_firestore`)
  AS 'CATALOGO_CAMBIO_REINICIAR_WORKFLOW';
ASSERT (SELECT COUNT(*)>0 AND COUNT(DISTINCT catalog_load_id)=1
  AND MIN(catalog_load_id)=p_catalog_load_id FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_dias_utp`)
  AS 'CATALOGO_CAMBIO_REINICIAR_WORKFLOW';
SET @@query_label = "project:mentores,component:mentores_pipeline,env:prd,team:data";

-- Consolidacion determinista de subatributos para la tabla final.
-- Valores faltantes/invalidos conservan la respuesta como incompleta (no son NA).
-- No modifica ni recalcula puntaje_total.
CREATE TEMP FUNCTION consolidar_resultado_mentor(calificaciones ARRAY<STRING>)
RETURNS STRING
AS (
  (
    SELECT CASE
      WHEN COUNT(*) = 0
        OR COUNTIF(calif IS NULL OR calif NOT IN ('1', '0', 'NA')) > 0 THEN NULL
      WHEN COUNTIF(calif = '0') > 0 THEN '0'
      WHEN COUNTIF(calif = 'NA') = COUNT(*) THEN 'NA'
      ELSE '1'
    END
    FROM (
      SELECT UPPER(TRIM(valor)) AS calif
      FROM UNNEST(calificaciones) AS valor
    )
  )
);

-- =====================================================================================
-- 1. ARMAR UNIVERSO A REVISAR EN CONTROL
-- Carga diaria: descubre audios disponibles hasta ayer, incluidos ingresos tardios.
-- Reproceso: evalua nuevamente los audios del rango inclusivo solicitado.
-- =====================================================================================

CREATE TEMP TABLE tmp_mentores_control_universo
AS

WITH audios_base AS (
  SELECT
    gcs_uri AS uri,
    file_name,
    gcs_path,
    source_gcs_uri,
    source_gcs_path,
    CAST(folder_uuid AS STRING) AS folder_uuid,
    DATE(fecha_audio) AS fecha_audio,
    SAFE_CAST(fecha_procesamiento AS TIMESTAMP) AS fecha_procesamiento,
    SAFE_CAST(file_size_bytes AS INT64) AS file_size_bytes,
    content_type,
    SAFE_CAST(duration_seconds AS FLOAT64) AS duration_seconds,
    audio_codec,
    SAFE_CAST(sample_rate_hz AS INT64) AS sample_rate_hz,
    SAFE_CAST(channels AS INT64) AS channels,
    format_name,
    SAFE_CAST(bit_rate AS INT64) AS bit_rate,
    sync_mode,
    copy_result
  FROM
    `prd-utpbi-data-operation.raw_cita_promotor.hist_cita_mentor_audio_catalog`
  WHERE gcs_uri IS NOT NULL
    AND copy_result = 'copied'
    AND COALESCE(SAFE_CAST(duration_seconds AS FLOAT64), 0) >= 60
    AND DATE(fecha_audio) <= p_date_to
    AND DATE(fecha_audio) >= p_date_from

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY gcs_uri
    ORDER BY
      SAFE_CAST(fecha_procesamiento AS TIMESTAMP) DESC,
      DATE(fecha_audio) DESC
  ) = 1
), audios_maestro AS (
  SELECT source_gcs_uri, COUNT(*) AS coincidencias,
    ANY_VALUE(STRUCT(audio_id, firestore_path, mentor_id, activityid,
      promotor_email, tipo_charla_mentor, tipos_charla_mentor_json)) AS dato
  FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_audios_mentores_firestore`
  WHERE catalog_load_id=p_catalog_load_id AND source_gcs_uri IS NOT NULL
  GROUP BY source_gcs_uri
), mentores_maestro AS (
  SELECT mentor_id, COUNT(*) AS coincidencias,
    ANY_VALUE(STRUCT(mentor_nombre, mentor_email, campus)) AS dato
  FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_mentores_firestore`
  WHERE catalog_load_id=p_catalog_load_id
  GROUP BY mentor_id
), eventos_maestro AS (
  SELECT doc_id, COUNT(*) AS coincidencias,
    ANY_VALUE(STRUCT(colegio, codigoModular, campus, fechaEvento)) AS dato
  FROM `prd-utpbi-data-operation.raw_cita_promotor.cat_dias_utp`
  WHERE catalog_load_id=p_catalog_load_id
  GROUP BY doc_id
), unidos AS (
  SELECT audios.*,
    a.dato.audio_id, a.dato.firestore_path, a.dato.mentor_id,
    IF(m.coincidencias = 1, m.dato.mentor_nombre, NULL) AS mentor_nombre,
    IF(m.coincidencias = 1, m.dato.mentor_email, NULL) AS mentor_email,
    IF(m.coincidencias = 1, m.dato.campus, NULL) AS mentor_campus,
    a.dato.promotor_email AS correo_grabador, a.dato.activityid,
    IF(d.coincidencias = 1, d.dato.colegio, NULL) AS colegio,
    IF(d.coincidencias = 1, d.dato.codigoModular, NULL) AS codigo_modular,
    IF(d.coincidencias = 1, d.dato.campus, NULL) AS campus_evento,
    IF(d.coincidencias = 1, d.dato.fechaEvento, NULL) AS fecha_evento,
    a.dato.tipo_charla_mentor AS tipo_original,
    a.dato.tipos_charla_mentor_json,
    ARRAY(
      SELECT DISTINCT TRIM(tipo)
      FROM UNNEST(IFNULL(JSON_VALUE_ARRAY(
        SAFE.PARSE_JSON(a.dato.tipos_charla_mentor_json)), ARRAY<STRING>[])) AS tipo
      WHERE NULLIF(TRIM(tipo), '') IS NOT NULL
    ) AS tipos_de_lista,
    CASE
      WHEN a.coincidencias IS NULL THEN 'SIN_AUDIO_FIRESTORE'
      WHEN a.coincidencias > 1 THEN 'AUDIO_AMBIGUO'
      WHEN m.coincidencias IS NULL THEN 'SIN_MENTOR_MAESTRO'
      WHEN m.coincidencias > 1 THEN 'MENTOR_AMBIGUO'
      ELSE 'OK'
    END AS estado_cruce
  FROM audios_base audios
  LEFT JOIN audios_maestro a ON audios.source_gcs_uri = a.source_gcs_uri
  LEFT JOIN mentores_maestro m ON a.coincidencias = 1 AND a.dato.mentor_id = m.mentor_id
  LEFT JOIN eventos_maestro d ON a.coincidencias = 1 AND a.dato.activityid = d.doc_id
), tipos AS (
  SELECT unidos.* EXCEPT(tipo_original, tipos_de_lista),
    COALESCE(NULLIF(TRIM(tipo_original), ''), tipos_de_lista[SAFE_OFFSET(0)]) AS tipo_charla_mentor,
    CASE
      WHEN estado_cruce != 'OK' THEN estado_cruce
      WHEN ARRAY_LENGTH(tipos_de_lista) > 1 THEN 'TIPO_CHARLA_AMBIGUO'
      WHEN NULLIF(TRIM(tipo_original), '') IS NOT NULL
        AND ARRAY_LENGTH(tipos_de_lista) = 1
        AND TRIM(tipo_original) != tipos_de_lista[SAFE_OFFSET(0)] THEN 'TIPO_CHARLA_CONTRADICTORIO'
      WHEN NULLIF(TRIM(tipo_original), '') IS NULL AND ARRAY_LENGTH(tipos_de_lista) = 0
        THEN 'SIN_TIPO_CHARLA'
      ELSE NULL
    END AS motivo_tipo
  FROM unidos
), rutas_prompt AS (
  SELECT tipos.*,
    CASE tipo_charla_mentor
      WHEN 'Conversatorio de último laboratorio' THEN v_prompt_laboratorio
      WHEN 'Charla de auditorio' THEN v_prompt_auditorio
      ELSE NULL
    END AS prompt_name
  FROM tipos
), prompts AS (
  SELECT prompt_name, COUNT(*) AS coincidencias,
    ANY_VALUE(STRUCT(prompt_text, LOWER(TO_HEX(SHA256(prompt_text))) AS prompt_sha256)) AS dato
  FROM `prd-utpbi-data-operation.raw_cita_promotor.sys_prompts`
  GROUP BY prompt_name
), preparados AS (
  SELECT r.* EXCEPT(motivo_tipo), p.dato.prompt_sha256,
    CASE
      WHEN r.motivo_tipo IS NOT NULL THEN r.motivo_tipo
      WHEN r.prompt_name IS NULL THEN 'TIPO_CHARLA_NO_SOPORTADO'
      WHEN p.coincidencias IS NULL OR NULLIF(TRIM(p.dato.prompt_text), '') IS NULL
        OR p.dato.prompt_sha256 IS NULL THEN 'PROMPT_NO_DISPONIBLE'
      WHEN p.coincidencias > 1 THEN 'PROMPT_AMBIGUO'
      ELSE NULL
    END AS motivo_preparacion
  FROM rutas_prompt r
  LEFT JOIN prompts p ON r.prompt_name = p.prompt_name
)
SELECT
  p.* EXCEPT(motivo_preparacion),
  CASE
    WHEN motivo_preparacion IS NOT NULL THEN 'PENDIENTE'
    WHEN v_reprocesar_fecha AND p.fecha_audio BETWEEN p_date_from AND p_date_to THEN 'PENDIENTE'
    WHEN EXISTS (
      SELECT 1
      FROM `prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd` final
      WHERE final.uri = p.uri AND final.prompt_name = p.prompt_name
        AND final.prompt_sha256 = p.prompt_sha256 AND final.mentor_id = p.mentor_id
        AND final.tipo_charla_mentor = p.tipo_charla_mentor
    ) THEN 'CARGADO'
    ELSE 'PENDIENTE'
  END AS estado_proceso,
  motivo_preparacion AS motivo_estado
FROM preparados p;

SET v_cantidad_audios_control = (
  SELECT COUNT(*)
  FROM
    `_SESSION.tmp_mentores_control_universo`
);

-- =====================================================================================
-- 2. UPSERT A TABLA DE CONTROL
-- Mantiene audios nuevos, pendientes, errores y cargados.
-- =====================================================================================

MERGE
  `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` AS T
USING (
  SELECT * EXCEPT(rn)
  FROM (
    SELECT
      universo.*,
      ROW_NUMBER() OVER (
        PARTITION BY uri
        ORDER BY
          fecha_procesamiento DESC,
          fecha_audio DESC
      ) AS rn
    FROM
      `_SESSION.tmp_mentores_control_universo`
        AS universo
  )
  WHERE rn = 1
) AS S
ON T.uri = S.uri

WHEN MATCHED THEN
UPDATE SET
  T.file_name = COALESCE(S.file_name, T.file_name),
  T.gcs_path = COALESCE(S.gcs_path, T.gcs_path),
  T.source_gcs_uri = COALESCE(S.source_gcs_uri, T.source_gcs_uri),
  T.source_gcs_path = COALESCE(S.source_gcs_path, T.source_gcs_path),
  T.folder_uuid = COALESCE(S.folder_uuid, T.folder_uuid),
  T.audio_id = S.audio_id,
  T.firestore_path = S.firestore_path,
  T.mentor_id = S.mentor_id,
  T.mentor_nombre = S.mentor_nombre,
  T.mentor_email = S.mentor_email,
  T.mentor_campus = S.mentor_campus,
  T.correo_grabador = S.correo_grabador,
  T.activityid = S.activityid,
  T.colegio = S.colegio,
  T.codigo_modular = S.codigo_modular,
  T.campus_evento = S.campus_evento,
  T.fecha_evento = S.fecha_evento,
  T.tipo_charla_mentor = S.tipo_charla_mentor,
  T.tipos_charla_mentor_json = S.tipos_charla_mentor_json,
  T.prompt_name = S.prompt_name,
  T.prompt_sha256 = S.prompt_sha256,
  T.estado_cruce = S.estado_cruce,
  T.fecha_audio = COALESCE(S.fecha_audio, T.fecha_audio),
  T.fecha_procesamiento = COALESCE(S.fecha_procesamiento, T.fecha_procesamiento),
  T.file_size_bytes = COALESCE(S.file_size_bytes, T.file_size_bytes),
  T.content_type = COALESCE(S.content_type, T.content_type),
  T.duration_seconds = COALESCE(S.duration_seconds, T.duration_seconds),
  T.audio_codec = COALESCE(S.audio_codec, T.audio_codec),
  T.sample_rate_hz = COALESCE(S.sample_rate_hz, T.sample_rate_hz),
  T.channels = COALESCE(S.channels, T.channels),
  T.format_name = COALESCE(S.format_name, T.format_name),
  T.bit_rate = COALESCE(S.bit_rate, T.bit_rate),
  T.sync_mode = COALESCE(S.sync_mode, T.sync_mode),
  T.copy_result = COALESCE(S.copy_result, T.copy_result),
  T.estado_proceso = CASE
    WHEN S.estado_proceso = 'CARGADO' THEN 'CARGADO'
    WHEN S.motivo_estado IS NOT NULL THEN 'PENDIENTE'
    WHEN T.estado_proceso = 'ERROR' AND NOT v_reprocesar_fecha THEN 'ERROR'
    ELSE 'PENDIENTE'
  END,
  T.motivo_estado = CASE
    WHEN S.estado_proceso = 'CARGADO' THEN NULL
    WHEN S.motivo_estado IS NOT NULL THEN S.motivo_estado
    WHEN T.estado_proceso = 'ERROR' AND NOT v_reprocesar_fecha
    THEN COALESCE(T.motivo_estado, S.motivo_estado)
    ELSE S.motivo_estado
  END,
  T.fecha_actualizacion = CURRENT_TIMESTAMP()

WHEN NOT MATCHED THEN
INSERT (
  uri,
  file_name,
  gcs_path,
  source_gcs_uri,
  source_gcs_path,
  folder_uuid,
  audio_id,
  firestore_path,
  mentor_id,
  mentor_nombre,
  mentor_email,
  mentor_campus,
  correo_grabador,
  activityid,
  colegio,
  codigo_modular,
  campus_evento,
  fecha_evento,
  tipo_charla_mentor,
  tipos_charla_mentor_json,
  prompt_name,
  prompt_sha256,
  estado_cruce,
  fecha_audio,
  fecha_procesamiento,
  file_size_bytes,
  content_type,
  duration_seconds,
  audio_codec,
  sample_rate_hz,
  channels,
  format_name,
  bit_rate,
  sync_mode,
  copy_result,
  estado_proceso,
  motivo_estado,
  fecha_registro,
  fecha_actualizacion
)
VALUES (
  S.uri,
  S.file_name,
  S.gcs_path,
  S.source_gcs_uri,
  S.source_gcs_path,
  S.folder_uuid,
  S.audio_id,
  S.firestore_path,
  S.mentor_id,
  S.mentor_nombre,
  S.mentor_email,
  S.mentor_campus,
  S.correo_grabador,
  S.activityid,
  S.colegio,
  S.codigo_modular,
  S.campus_evento,
  S.fecha_evento,
  S.tipo_charla_mentor,
  S.tipos_charla_mentor_json,
  S.prompt_name,
  S.prompt_sha256,
  S.estado_cruce,
  S.fecha_audio,
  S.fecha_procesamiento,
  S.file_size_bytes,
  S.content_type,
  S.duration_seconds,
  S.audio_codec,
  S.sample_rate_hz,
  S.channels,
  S.format_name,
  S.bit_rate,
  S.sync_mode,
  S.copy_result,
  S.estado_proceso,
  S.motivo_estado,
  CURRENT_TIMESTAMP(),
  CURRENT_TIMESTAMP()
);

-- =====================================================================================
-- 3. VALIDAR Y CONSTRUIR LISTA DE AUDIOS QUE SI PASAN A GEMINI
-- Gemini recibe audios en estado pendiente/error desde la tabla de control.
-- =====================================================================================

CREATE TEMP TABLE tmp_mentores_audios_gemini
AS
SELECT
  *
FROM
  `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control`
WHERE uri IN (SELECT uri FROM `_SESSION.tmp_mentores_control_universo`)
  AND estado_proceso IN ('PENDIENTE', 'ERROR')
  AND (motivo_estado IS NULL OR estado_proceso = 'ERROR')
  AND estado_cruce = 'OK'
  AND tipo_charla_mentor IN ('Conversatorio de último laboratorio', 'Charla de auditorio')
  AND prompt_name IS NOT NULL AND prompt_sha256 IS NOT NULL
  AND uri IS NOT NULL
  AND (
    fecha_audio <= v_fecha_proceso
    OR fecha_audio IS NULL
    OR estado_proceso = 'ERROR'
  )
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY uri
  ORDER BY
    IF(fecha_audio = v_fecha_proceso, 1, 0) DESC,
    fecha_actualizacion DESC,
    fecha_registro DESC
) = 1;

  SET v_cantidad_audios_gemini = (
    SELECT COUNT(DISTINCT uri)
    FROM `_SESSION.tmp_mentores_audios_gemini`
  );

  SET v_uris = (
    SELECT TO_JSON_STRING(ARRAY_AGG(DISTINCT uri IGNORE NULLS))
    FROM `_SESSION.tmp_mentores_audios_gemini`
  );

  IF v_cantidad_audios_gemini > 0 THEN

    SET v_sql = FORMAT("""
      CREATE OR REPLACE EXTERNAL TABLE
        `prd-utpbi-data-operation.raw_cita_promotor.tmp_mentores_external`
      WITH CONNECTION
        `prd-utpbi-data-operation.US.utp_gen_ia_process`
      OPTIONS (
        expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 2 DAY),
        object_metadata = 'SIMPLE',
        uris = %s,
        max_staleness = INTERVAL 30 MINUTE,
        metadata_cache_mode = AUTOMATIC
      )
    """, v_uris);

    EXECUTE IMMEDIATE v_sql;

    CREATE TEMP TABLE tmp_mentores_audios_prompt
AS
    SELECT
      control.*,
      ext.ref AS audio_object,
      COALESCE(prompt_tipo.prompt_text, '') AS prompt_text
    FROM
      `_SESSION.tmp_mentores_audios_gemini`
        AS control
    INNER JOIN
      `prd-utpbi-data-operation.raw_cita_promotor.tmp_mentores_external`
        AS ext
      ON ext.uri = control.uri
    LEFT JOIN (
      SELECT prompt_name, LOWER(TO_HEX(SHA256(prompt_text))) AS prompt_sha256,
        ANY_VALUE(prompt_text) AS prompt_text
      FROM `prd-utpbi-data-operation.raw_cita_promotor.sys_prompts`
      GROUP BY prompt_name, LOWER(TO_HEX(SHA256(prompt_text)))
      HAVING COUNT(*) = 1
    ) AS prompt_tipo
      ON control.prompt_name = prompt_tipo.prompt_name
      AND control.prompt_sha256 = prompt_tipo.prompt_sha256;

    UPDATE
      `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` AS control
    SET
      estado_proceso = 'PENDIENTE',
      motivo_estado = 'PROMPT_NO_DISPONIBLE',
      fecha_actualizacion = CURRENT_TIMESTAMP()
    WHERE EXISTS (
      SELECT 1
      FROM
        `_SESSION.tmp_mentores_audios_prompt`
          AS prompt
      WHERE prompt.uri = control.uri
        AND (prompt.prompt_text IS NULL OR TRIM(prompt.prompt_text) = '')
    );

    CREATE TEMP TABLE tmp_mentores_ia_results
AS
    SELECT ia.*
    FROM AI.GENERATE_TABLE(
      MODEL `prd-utpbi-data-operation.adf_speech_analytics.gemini-2-5-flash`,
      (
        SELECT
          STRUCT(
            CONCAT(
              prompt_text,
              '\n\n',
              'INSTRUCCION TECNICA ADICIONAL:', '\n',
              'Analiza exclusivamente el audio adjunto.', '\n',
              'La respuesta sera capturada mediante el output_schema de BigQuery.', '\n',
              'Devuelve unicamente los valores solicitados para cada columna del esquema de salida.', '\n',
              'No devuelvas JSON manual.', '\n',
              'No devuelvas arreglos, objetos, llaves, markdown ni texto adicional.', '\n',
              'Evalua todos los atributos y completa todas las columnas del esquema.', '\n',
              'No inventes, reformules, reemplaces ni agregues nombres de campos.', '\n',
              'Para los campos *_calif devuelve unicamente "1", "0" o "NA".', '\n',
              'Para los campos *_resultado devuelve unicamente "1" o "0"; nunca devuelvas "NA".', '\n',
              'Si todos los subatributos de un atributo principal son "NA", marca *_resultado como "0".', '\n',
              'Para los campos *_motivo devuelve evidencia o justificacion, maximo 20 palabras.', '\n',
              'El campo puntaje_total debe ser numerico entre 0 y 100.', '\n',
              'El campo resumen_general debe salir como ultimo campo, maximo 18 palabras, sin repetir ideas.', '\n',
              'No devuelvas valores null.', '\n',
              'No cites frases largas del transcript.', '\n',
              'No uses comillas simples ni dobles dentro de los motivos.', '\n',
              'No incluyas saltos de linea dentro de los valores.'
            ) AS instruction,
            audio_object AS audio_object
          ) AS prompt,
          * EXCEPT(prompt_text)
        FROM
          `_SESSION.tmp_mentores_audios_prompt`
        WHERE audio_object IS NOT NULL
          AND prompt_text IS NOT NULL
          AND TRIM(prompt_text) != ''
      ),
      STRUCT(
        '''
        puntaje_total FLOAT64,
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
        resumen_general STRING
        ''' AS output_schema,
        4096 AS max_output_tokens,
        0 AS temperature
      )
    ) AS ia;

    SET v_cantidad_resultados = (
      SELECT COUNT(*)
      FROM `_SESSION.tmp_mentores_ia_results`
    );

    CREATE TEMP TABLE tmp_mentores_json
AS
    WITH raw_response AS (
      SELECT
        ia.*,
        SAFE.PARSE_JSON(
          REPLACE(
            COALESCE(
              REGEXP_EXTRACT(
                TRIM(JSON_VALUE(full_response, '$.candidates[0].content.parts[0].text')),
                r'(\{[\s\S]*\})'
              ),
              TRIM(JSON_VALUE(full_response, '$.candidates[0].content.parts[0].text'))
            ),
            r'\_',
            '_'
          )
        ) AS fallback_json
      FROM
        `_SESSION.tmp_mentores_ia_results`
          AS ia
    ),
    normalized AS (
      SELECT
        raw.* EXCEPT(
          puntaje_total,
          resumen_general,
          b1_comunicacion_oral_resultado,
          b1_claridad_discurso_calif,
          b1_claridad_discurso_motivo,
          b1_estructura_mensaje_calif,
          b1_estructura_mensaje_motivo,
          b1_palabras_clave_calif,
          b1_palabras_clave_motivo,
          b1_generacion_impacto_resultado,
          b1_mensajes_motivacionales_calif,
          b1_mensajes_motivacionales_motivo,
          b1_cierre_estructurado_calif,
          b1_cierre_estructurado_motivo,
          b1_conexion_engagement_resultado,
          b1_empatia_cercania_calif,
          b1_empatia_cercania_motivo,
          b1_interaccion_activacion_publico_calif,
          b1_interaccion_activacion_publico_motivo,
          b2_conocimiento_institucional_resultado,
          b2_precision_informacion_calif,
          b2_precision_informacion_motivo,
          b2_cobertura_temas_calif,
          b2_cobertura_temas_motivo,
          b2_dominio_narrativa_resultado,
          b2_storytelling_calif,
          b2_storytelling_motivo,
          b2_liderazgo_resultado,
          b2_cumplimiento_duracion_estructura_calif,
          b2_cumplimiento_duracion_estructura_motivo,
          b2_manejo_interrupciones_calif,
          b2_manejo_interrupciones_motivo,
          b3_orientacion_servicio_resultado,
          b3_calidad_respuestas_calif,
          b3_calidad_respuestas_motivo,
          b3_seguridad_responder_calif,
          b3_seguridad_responder_motivo,
          b3_refuerzo_institucional_calif,
          b3_refuerzo_institucional_motivo,
          fallback_json
        ),
        fallback_json IS NOT NULL AS fallback_json_valido,
        COALESCE(puntaje_total, SAFE_CAST(JSON_VALUE(fallback_json, '$.puntaje_total') AS FLOAT64)) AS puntaje_total,
        COALESCE(NULLIF(resumen_general, ''), JSON_VALUE(fallback_json, '$.resumen_general')) AS resumen_general,
        COALESCE(NULLIF(b1_comunicacion_oral_resultado, ''), JSON_VALUE(fallback_json, '$.b1_comunicacion_oral_resultado')) AS b1_comunicacion_oral_resultado,
        COALESCE(NULLIF(b1_claridad_discurso_calif, ''), JSON_VALUE(fallback_json, '$.b1_claridad_discurso_calif')) AS b1_claridad_discurso_calif,
        COALESCE(NULLIF(b1_claridad_discurso_motivo, ''), JSON_VALUE(fallback_json, '$.b1_claridad_discurso_motivo')) AS b1_claridad_discurso_motivo,
        COALESCE(NULLIF(b1_estructura_mensaje_calif, ''), JSON_VALUE(fallback_json, '$.b1_estructura_mensaje_calif')) AS b1_estructura_mensaje_calif,
        COALESCE(NULLIF(b1_estructura_mensaje_motivo, ''), JSON_VALUE(fallback_json, '$.b1_estructura_mensaje_motivo')) AS b1_estructura_mensaje_motivo,
        COALESCE(NULLIF(b1_palabras_clave_calif, ''), JSON_VALUE(fallback_json, '$.b1_palabras_clave_calif')) AS b1_palabras_clave_calif,
        COALESCE(NULLIF(b1_palabras_clave_motivo, ''), JSON_VALUE(fallback_json, '$.b1_palabras_clave_motivo')) AS b1_palabras_clave_motivo,
        COALESCE(NULLIF(b1_generacion_impacto_resultado, ''), JSON_VALUE(fallback_json, '$.b1_generacion_impacto_resultado')) AS b1_generacion_impacto_resultado,
        COALESCE(NULLIF(b1_mensajes_motivacionales_calif, ''), JSON_VALUE(fallback_json, '$.b1_mensajes_motivacionales_calif')) AS b1_mensajes_motivacionales_calif,
        COALESCE(NULLIF(b1_mensajes_motivacionales_motivo, ''), JSON_VALUE(fallback_json, '$.b1_mensajes_motivacionales_motivo')) AS b1_mensajes_motivacionales_motivo,
        COALESCE(NULLIF(b1_cierre_estructurado_calif, ''), JSON_VALUE(fallback_json, '$.b1_cierre_estructurado_calif')) AS b1_cierre_estructurado_calif,
        COALESCE(NULLIF(b1_cierre_estructurado_motivo, ''), JSON_VALUE(fallback_json, '$.b1_cierre_estructurado_motivo')) AS b1_cierre_estructurado_motivo,
        COALESCE(NULLIF(b1_conexion_engagement_resultado, ''), JSON_VALUE(fallback_json, '$.b1_conexion_engagement_resultado')) AS b1_conexion_engagement_resultado,
        COALESCE(NULLIF(b1_empatia_cercania_calif, ''), JSON_VALUE(fallback_json, '$.b1_empatia_cercania_calif')) AS b1_empatia_cercania_calif,
        COALESCE(NULLIF(b1_empatia_cercania_motivo, ''), JSON_VALUE(fallback_json, '$.b1_empatia_cercania_motivo')) AS b1_empatia_cercania_motivo,
        COALESCE(NULLIF(b1_interaccion_activacion_publico_calif, ''), JSON_VALUE(fallback_json, '$.b1_interaccion_activacion_publico_calif')) AS b1_interaccion_activacion_publico_calif,
        COALESCE(NULLIF(b1_interaccion_activacion_publico_motivo, ''), JSON_VALUE(fallback_json, '$.b1_interaccion_activacion_publico_motivo')) AS b1_interaccion_activacion_publico_motivo,
        COALESCE(NULLIF(b2_conocimiento_institucional_resultado, ''), JSON_VALUE(fallback_json, '$.b2_conocimiento_institucional_resultado')) AS b2_conocimiento_institucional_resultado,
        COALESCE(NULLIF(b2_precision_informacion_calif, ''), JSON_VALUE(fallback_json, '$.b2_precision_informacion_calif')) AS b2_precision_informacion_calif,
        COALESCE(NULLIF(b2_precision_informacion_motivo, ''), JSON_VALUE(fallback_json, '$.b2_precision_informacion_motivo')) AS b2_precision_informacion_motivo,
        COALESCE(NULLIF(b2_cobertura_temas_calif, ''), JSON_VALUE(fallback_json, '$.b2_cobertura_temas_calif')) AS b2_cobertura_temas_calif,
        COALESCE(NULLIF(b2_cobertura_temas_motivo, ''), JSON_VALUE(fallback_json, '$.b2_cobertura_temas_motivo')) AS b2_cobertura_temas_motivo,
        COALESCE(NULLIF(b2_dominio_narrativa_resultado, ''), JSON_VALUE(fallback_json, '$.b2_dominio_narrativa_resultado')) AS b2_dominio_narrativa_resultado,
        COALESCE(NULLIF(b2_storytelling_calif, ''), JSON_VALUE(fallback_json, '$.b2_storytelling_calif')) AS b2_storytelling_calif,
        COALESCE(NULLIF(b2_storytelling_motivo, ''), JSON_VALUE(fallback_json, '$.b2_storytelling_motivo')) AS b2_storytelling_motivo,
        COALESCE(NULLIF(b2_liderazgo_resultado, ''), JSON_VALUE(fallback_json, '$.b2_liderazgo_resultado')) AS b2_liderazgo_resultado,
        COALESCE(NULLIF(b2_cumplimiento_duracion_estructura_calif, ''), JSON_VALUE(fallback_json, '$.b2_cumplimiento_duracion_estructura_calif')) AS b2_cumplimiento_duracion_estructura_calif,
        COALESCE(NULLIF(b2_cumplimiento_duracion_estructura_motivo, ''), JSON_VALUE(fallback_json, '$.b2_cumplimiento_duracion_estructura_motivo')) AS b2_cumplimiento_duracion_estructura_motivo,
        COALESCE(NULLIF(b2_manejo_interrupciones_calif, ''), JSON_VALUE(fallback_json, '$.b2_manejo_interrupciones_calif')) AS b2_manejo_interrupciones_calif,
        COALESCE(NULLIF(b2_manejo_interrupciones_motivo, ''), JSON_VALUE(fallback_json, '$.b2_manejo_interrupciones_motivo')) AS b2_manejo_interrupciones_motivo,
        COALESCE(NULLIF(b3_orientacion_servicio_resultado, ''), JSON_VALUE(fallback_json, '$.b3_orientacion_servicio_resultado')) AS b3_orientacion_servicio_resultado,
        COALESCE(NULLIF(b3_calidad_respuestas_calif, ''), JSON_VALUE(fallback_json, '$.b3_calidad_respuestas_calif')) AS b3_calidad_respuestas_calif,
        COALESCE(NULLIF(b3_calidad_respuestas_motivo, ''), JSON_VALUE(fallback_json, '$.b3_calidad_respuestas_motivo')) AS b3_calidad_respuestas_motivo,
        COALESCE(NULLIF(b3_seguridad_responder_calif, ''), JSON_VALUE(fallback_json, '$.b3_seguridad_responder_calif')) AS b3_seguridad_responder_calif,
        COALESCE(NULLIF(b3_seguridad_responder_motivo, ''), JSON_VALUE(fallback_json, '$.b3_seguridad_responder_motivo')) AS b3_seguridad_responder_motivo,
        COALESCE(NULLIF(b3_refuerzo_institucional_calif, ''), JSON_VALUE(fallback_json, '$.b3_refuerzo_institucional_calif')) AS b3_refuerzo_institucional_calif,
        COALESCE(NULLIF(b3_refuerzo_institucional_motivo, ''), JSON_VALUE(fallback_json, '$.b3_refuerzo_institucional_motivo')) AS b3_refuerzo_institucional_motivo
      FROM raw_response AS raw
    ),
    consolidated AS (
      -- Reemplaza los resultados de la IA antes de serializar y validar.
      -- Los 15 subatributos, motivos y puntaje_total permanecen intactos.
      SELECT normalized.* REPLACE (
        consolidar_resultado_mentor([
          b1_claridad_discurso_calif,
          b1_estructura_mensaje_calif,
          b1_palabras_clave_calif
        ]) AS b1_comunicacion_oral_resultado,
        consolidar_resultado_mentor([
          b1_mensajes_motivacionales_calif,
          b1_cierre_estructurado_calif
        ]) AS b1_generacion_impacto_resultado,
        consolidar_resultado_mentor([
          b1_empatia_cercania_calif,
          b1_interaccion_activacion_publico_calif
        ]) AS b1_conexion_engagement_resultado,
        consolidar_resultado_mentor([
          b2_precision_informacion_calif,
          b2_cobertura_temas_calif
        ]) AS b2_conocimiento_institucional_resultado,
        consolidar_resultado_mentor([
          b2_storytelling_calif
        ]) AS b2_dominio_narrativa_resultado,
        consolidar_resultado_mentor([
          b2_cumplimiento_duracion_estructura_calif,
          b2_manejo_interrupciones_calif
        ]) AS b2_liderazgo_resultado,
        consolidar_resultado_mentor([
          b3_calidad_respuestas_calif,
          b3_seguridad_responder_calif,
          b3_refuerzo_institucional_calif
        ]) AS b3_orientacion_servicio_resultado
      )
      FROM normalized
    ),
    cleaned AS (
      SELECT
        consolidated.*,
        TO_JSON_STRING(STRUCT(
          puntaje_total,
          resumen_general,
          b1_comunicacion_oral_resultado,
          b1_claridad_discurso_calif,
          b1_claridad_discurso_motivo,
          b1_estructura_mensaje_calif,
          b1_estructura_mensaje_motivo,
          b1_palabras_clave_calif,
          b1_palabras_clave_motivo,
          b1_generacion_impacto_resultado,
          b1_mensajes_motivacionales_calif,
          b1_mensajes_motivacionales_motivo,
          b1_cierre_estructurado_calif,
          b1_cierre_estructurado_motivo,
          b1_conexion_engagement_resultado,
          b1_empatia_cercania_calif,
          b1_empatia_cercania_motivo,
          b1_interaccion_activacion_publico_calif,
          b1_interaccion_activacion_publico_motivo,
          b2_conocimiento_institucional_resultado,
          b2_precision_informacion_calif,
          b2_precision_informacion_motivo,
          b2_cobertura_temas_calif,
          b2_cobertura_temas_motivo,
          b2_dominio_narrativa_resultado,
          b2_storytelling_calif,
          b2_storytelling_motivo,
          b2_liderazgo_resultado,
          b2_cumplimiento_duracion_estructura_calif,
          b2_cumplimiento_duracion_estructura_motivo,
          b2_manejo_interrupciones_calif,
          b2_manejo_interrupciones_motivo,
          b3_orientacion_servicio_resultado,
          b3_calidad_respuestas_calif,
          b3_calidad_respuestas_motivo,
          b3_seguridad_responder_calif,
          b3_seguridad_responder_motivo,
          b3_refuerzo_institucional_calif,
          b3_refuerzo_institucional_motivo
        )) AS clean_json_text
      FROM consolidated
    )
    SELECT
      cleaned.*,
      SAFE.PARSE_JSON(clean_json_text) AS final_json,
      (
        (COALESCE(status, '') = '' OR fallback_json_valido)
        AND puntaje_total IS NOT NULL
        AND b1_comunicacion_oral_resultado IS NOT NULL
        AND b1_generacion_impacto_resultado IS NOT NULL
        AND b1_conexion_engagement_resultado IS NOT NULL
        AND b2_conocimiento_institucional_resultado IS NOT NULL
        AND b2_dominio_narrativa_resultado IS NOT NULL
        AND b2_liderazgo_resultado IS NOT NULL
        AND b3_orientacion_servicio_resultado IS NOT NULL
        AND resumen_general IS NOT NULL
        AND CHAR_LENGTH(resumen_general) <= 300
      ) AS json_valido,
      CASE
        WHEN COALESCE(status, '') != '' AND NOT fallback_json_valido THEN status
        WHEN resumen_general IS NOT NULL AND CHAR_LENGTH(resumen_general) > 300
        THEN 'RESUMEN_INVALIDO'
        WHEN puntaje_total IS NULL
          OR b1_comunicacion_oral_resultado IS NULL
          OR b1_generacion_impacto_resultado IS NULL
          OR b1_conexion_engagement_resultado IS NULL
          OR b2_conocimiento_institucional_resultado IS NULL
          OR b2_dominio_narrativa_resultado IS NULL
          OR b2_liderazgo_resultado IS NULL
          OR b3_orientacion_servicio_resultado IS NULL
          OR resumen_general IS NULL
        THEN 'RESPUESTA_INCOMPLETA'
        ELSE NULL
      END AS json_error_tipo
    FROM cleaned;

    SET v_cantidad_respuestas_error = (
      SELECT COUNTIF(NOT json_valido)
      FROM `_SESSION.tmp_mentores_json`
    );

  ELSE

    CREATE TEMP TABLE tmp_mentores_json
AS
    SELECT
      control.*,
      CAST(NULL AS STRING) AS audio_object,
      CAST(NULL AS JSON) AS full_response,
      CAST(NULL AS STRING) AS status,
      CAST(NULL AS FLOAT64) AS puntaje_total,
      CAST(NULL AS STRING) AS resumen_general,
      CAST(NULL AS STRING) AS b1_comunicacion_oral_resultado,
      CAST(NULL AS STRING) AS b1_claridad_discurso_calif,
      CAST(NULL AS STRING) AS b1_claridad_discurso_motivo,
      CAST(NULL AS STRING) AS b1_estructura_mensaje_calif,
      CAST(NULL AS STRING) AS b1_estructura_mensaje_motivo,
      CAST(NULL AS STRING) AS b1_palabras_clave_calif,
      CAST(NULL AS STRING) AS b1_palabras_clave_motivo,
      CAST(NULL AS STRING) AS b1_generacion_impacto_resultado,
      CAST(NULL AS STRING) AS b1_mensajes_motivacionales_calif,
      CAST(NULL AS STRING) AS b1_mensajes_motivacionales_motivo,
      CAST(NULL AS STRING) AS b1_cierre_estructurado_calif,
      CAST(NULL AS STRING) AS b1_cierre_estructurado_motivo,
      CAST(NULL AS STRING) AS b1_conexion_engagement_resultado,
      CAST(NULL AS STRING) AS b1_empatia_cercania_calif,
      CAST(NULL AS STRING) AS b1_empatia_cercania_motivo,
      CAST(NULL AS STRING) AS b1_interaccion_activacion_publico_calif,
      CAST(NULL AS STRING) AS b1_interaccion_activacion_publico_motivo,
      CAST(NULL AS STRING) AS b2_conocimiento_institucional_resultado,
      CAST(NULL AS STRING) AS b2_precision_informacion_calif,
      CAST(NULL AS STRING) AS b2_precision_informacion_motivo,
      CAST(NULL AS STRING) AS b2_cobertura_temas_calif,
      CAST(NULL AS STRING) AS b2_cobertura_temas_motivo,
      CAST(NULL AS STRING) AS b2_dominio_narrativa_resultado,
      CAST(NULL AS STRING) AS b2_storytelling_calif,
      CAST(NULL AS STRING) AS b2_storytelling_motivo,
      CAST(NULL AS STRING) AS b2_liderazgo_resultado,
      CAST(NULL AS STRING) AS b2_cumplimiento_duracion_estructura_calif,
      CAST(NULL AS STRING) AS b2_cumplimiento_duracion_estructura_motivo,
      CAST(NULL AS STRING) AS b2_manejo_interrupciones_calif,
      CAST(NULL AS STRING) AS b2_manejo_interrupciones_motivo,
      CAST(NULL AS STRING) AS b3_orientacion_servicio_resultado,
      CAST(NULL AS STRING) AS b3_calidad_respuestas_calif,
      CAST(NULL AS STRING) AS b3_calidad_respuestas_motivo,
      CAST(NULL AS STRING) AS b3_seguridad_responder_calif,
      CAST(NULL AS STRING) AS b3_seguridad_responder_motivo,
      CAST(NULL AS STRING) AS b3_refuerzo_institucional_calif,
      CAST(NULL AS STRING) AS b3_refuerzo_institucional_motivo,
      CAST(NULL AS STRING) AS clean_json_text,
      CAST(NULL AS JSON) AS final_json,
      FALSE AS json_valido,
      CAST(NULL AS STRING) AS json_error_tipo
    FROM
      `_SESSION.tmp_mentores_audios_gemini`
        AS control
    WHERE FALSE;

    SET v_cantidad_resultados = 0;
    SET v_cantidad_respuestas_error = 0;

  END IF;

  BEGIN TRANSACTION;
  INSERT INTO `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_ia_respuestas`
  SELECT p_run_id, CURRENT_TIMESTAMP(), uri, full_response, clean_json_text,
         final_json, json_valido, json_error_tipo
  FROM `_SESSION.tmp_mentores_json`;
  INSERT INTO `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_evaluaciones`
  SELECT p_run_id AS run_id, CURRENT_TIMESTAMP() AS evaluated_at, result.*
  FROM (
    SELECT
      v_fecha_proceso AS process_date,
      resultado.uri,
      COALESCE(resultado.file_name, REGEXP_EXTRACT(resultado.uri, r'([^/]+)$')) AS audio,
      resultado.gcs_path,
      resultado.source_gcs_uri,
      resultado.folder_uuid,
      resultado.audio_id,
      resultado.firestore_path,
      resultado.mentor_id,
      resultado.mentor_nombre,
      resultado.mentor_email,
      resultado.mentor_campus,
      resultado.correo_grabador,
      resultado.activityid,
      resultado.colegio,
      resultado.codigo_modular,
      resultado.campus_evento,
      resultado.fecha_evento,
      resultado.tipo_charla_mentor,
      resultado.tipos_charla_mentor_json,
      resultado.prompt_name,
      resultado.prompt_sha256,
      resultado.estado_cruce,
      resultado.fecha_audio,
      resultado.duration_seconds AS duracion,
      resultado.clean_json_text AS json_text,
      resultado.final_json,
      resultado.full_response,
      resultado.status,

      resultado.puntaje_total,
      resultado.resumen_general,
      resultado.b1_comunicacion_oral_resultado,
      resultado.b1_claridad_discurso_calif,
      resultado.b1_claridad_discurso_motivo,
      resultado.b1_estructura_mensaje_calif,
      resultado.b1_estructura_mensaje_motivo,
      resultado.b1_palabras_clave_calif,
      resultado.b1_palabras_clave_motivo,
      resultado.b1_generacion_impacto_resultado,
      resultado.b1_mensajes_motivacionales_calif,
      resultado.b1_mensajes_motivacionales_motivo,
      resultado.b1_cierre_estructurado_calif,
      resultado.b1_cierre_estructurado_motivo,
      resultado.b1_conexion_engagement_resultado,
      resultado.b1_empatia_cercania_calif,
      resultado.b1_empatia_cercania_motivo,
      resultado.b1_interaccion_activacion_publico_calif,
      resultado.b1_interaccion_activacion_publico_motivo,
      resultado.b2_conocimiento_institucional_resultado,
      resultado.b2_precision_informacion_calif,
      resultado.b2_precision_informacion_motivo,
      resultado.b2_cobertura_temas_calif,
      resultado.b2_cobertura_temas_motivo,
      resultado.b2_dominio_narrativa_resultado,
      resultado.b2_storytelling_calif,
      resultado.b2_storytelling_motivo,
      resultado.b2_liderazgo_resultado,
      resultado.b2_cumplimiento_duracion_estructura_calif,
      resultado.b2_cumplimiento_duracion_estructura_motivo,
      resultado.b2_manejo_interrupciones_calif,
      resultado.b2_manejo_interrupciones_motivo,
      resultado.b3_orientacion_servicio_resultado,
      resultado.b3_calidad_respuestas_calif,
      resultado.b3_calidad_respuestas_motivo,
      resultado.b3_seguridad_responder_calif,
      resultado.b3_seguridad_responder_motivo,
      resultado.b3_refuerzo_institucional_calif,
      resultado.b3_refuerzo_institucional_motivo,

      DATETIME(CURRENT_TIMESTAMP(), 'America/Lima') AS load_date
    FROM
      `_SESSION.tmp_mentores_json`
        AS resultado
    WHERE json_valido
  ) result;
  MERGE
    `prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd` AS T
  USING (
    SELECT
      v_fecha_proceso AS process_date,
      resultado.uri,
      COALESCE(resultado.file_name, REGEXP_EXTRACT(resultado.uri, r'([^/]+)$')) AS audio,
      resultado.gcs_path,
      resultado.source_gcs_uri,
      resultado.folder_uuid,
      resultado.audio_id,
      resultado.firestore_path,
      resultado.mentor_id,
      resultado.mentor_nombre,
      resultado.mentor_email,
      resultado.mentor_campus,
      resultado.correo_grabador,
      resultado.activityid,
      resultado.colegio,
      resultado.codigo_modular,
      resultado.campus_evento,
      resultado.fecha_evento,
      resultado.tipo_charla_mentor,
      resultado.tipos_charla_mentor_json,
      resultado.prompt_name,
      resultado.prompt_sha256,
      resultado.estado_cruce,
      resultado.fecha_audio,
      resultado.duration_seconds AS duracion,
      resultado.clean_json_text AS json_text,
      resultado.final_json,
      resultado.full_response,
      resultado.status,

      resultado.puntaje_total,
      resultado.resumen_general,
      resultado.b1_comunicacion_oral_resultado,
      resultado.b1_claridad_discurso_calif,
      resultado.b1_claridad_discurso_motivo,
      resultado.b1_estructura_mensaje_calif,
      resultado.b1_estructura_mensaje_motivo,
      resultado.b1_palabras_clave_calif,
      resultado.b1_palabras_clave_motivo,
      resultado.b1_generacion_impacto_resultado,
      resultado.b1_mensajes_motivacionales_calif,
      resultado.b1_mensajes_motivacionales_motivo,
      resultado.b1_cierre_estructurado_calif,
      resultado.b1_cierre_estructurado_motivo,
      resultado.b1_conexion_engagement_resultado,
      resultado.b1_empatia_cercania_calif,
      resultado.b1_empatia_cercania_motivo,
      resultado.b1_interaccion_activacion_publico_calif,
      resultado.b1_interaccion_activacion_publico_motivo,
      resultado.b2_conocimiento_institucional_resultado,
      resultado.b2_precision_informacion_calif,
      resultado.b2_precision_informacion_motivo,
      resultado.b2_cobertura_temas_calif,
      resultado.b2_cobertura_temas_motivo,
      resultado.b2_dominio_narrativa_resultado,
      resultado.b2_storytelling_calif,
      resultado.b2_storytelling_motivo,
      resultado.b2_liderazgo_resultado,
      resultado.b2_cumplimiento_duracion_estructura_calif,
      resultado.b2_cumplimiento_duracion_estructura_motivo,
      resultado.b2_manejo_interrupciones_calif,
      resultado.b2_manejo_interrupciones_motivo,
      resultado.b3_orientacion_servicio_resultado,
      resultado.b3_calidad_respuestas_calif,
      resultado.b3_calidad_respuestas_motivo,
      resultado.b3_seguridad_responder_calif,
      resultado.b3_seguridad_responder_motivo,
      resultado.b3_refuerzo_institucional_calif,
      resultado.b3_refuerzo_institucional_motivo,

      DATETIME(CURRENT_TIMESTAMP(), 'America/Lima') AS load_date
    FROM
      `_SESSION.tmp_mentores_json`
        AS resultado
    WHERE json_valido
  ) AS S
  ON T.uri = S.uri
  WHEN MATCHED THEN
    UPDATE SET
      T.process_date = S.process_date,
      T.audio = S.audio,
      T.gcs_path = S.gcs_path,
      T.source_gcs_uri = S.source_gcs_uri,
      T.folder_uuid = S.folder_uuid,
      T.audio_id = S.audio_id,
      T.firestore_path = S.firestore_path,
      T.mentor_id = S.mentor_id,
      T.mentor_nombre = S.mentor_nombre,
      T.mentor_email = S.mentor_email,
      T.mentor_campus = S.mentor_campus,
      T.correo_grabador = S.correo_grabador,
      T.activityid = S.activityid,
      T.colegio = S.colegio,
      T.codigo_modular = S.codigo_modular,
      T.campus_evento = S.campus_evento,
      T.fecha_evento = S.fecha_evento,
      T.tipo_charla_mentor = S.tipo_charla_mentor,
      T.tipos_charla_mentor_json = S.tipos_charla_mentor_json,
      T.prompt_name = S.prompt_name,
      T.prompt_sha256 = S.prompt_sha256,
      T.estado_cruce = S.estado_cruce,
      T.fecha_audio = S.fecha_audio,
      T.duracion = S.duracion,
      T.json_text = S.json_text,
      T.final_json = S.final_json,
      T.full_response = S.full_response,
      T.status = S.status,
      T.puntaje_total = S.puntaje_total,
      T.resumen_general = S.resumen_general,
      T.b1_comunicacion_oral_resultado = S.b1_comunicacion_oral_resultado,
      T.b1_claridad_discurso_calif = S.b1_claridad_discurso_calif,
      T.b1_claridad_discurso_motivo = S.b1_claridad_discurso_motivo,
      T.b1_estructura_mensaje_calif = S.b1_estructura_mensaje_calif,
      T.b1_estructura_mensaje_motivo = S.b1_estructura_mensaje_motivo,
      T.b1_palabras_clave_calif = S.b1_palabras_clave_calif,
      T.b1_palabras_clave_motivo = S.b1_palabras_clave_motivo,
      T.b1_generacion_impacto_resultado = S.b1_generacion_impacto_resultado,
      T.b1_mensajes_motivacionales_calif = S.b1_mensajes_motivacionales_calif,
      T.b1_mensajes_motivacionales_motivo = S.b1_mensajes_motivacionales_motivo,
      T.b1_cierre_estructurado_calif = S.b1_cierre_estructurado_calif,
      T.b1_cierre_estructurado_motivo = S.b1_cierre_estructurado_motivo,
      T.b1_conexion_engagement_resultado = S.b1_conexion_engagement_resultado,
      T.b1_empatia_cercania_calif = S.b1_empatia_cercania_calif,
      T.b1_empatia_cercania_motivo = S.b1_empatia_cercania_motivo,
      T.b1_interaccion_activacion_publico_calif = S.b1_interaccion_activacion_publico_calif,
      T.b1_interaccion_activacion_publico_motivo = S.b1_interaccion_activacion_publico_motivo,
      T.b2_conocimiento_institucional_resultado = S.b2_conocimiento_institucional_resultado,
      T.b2_precision_informacion_calif = S.b2_precision_informacion_calif,
      T.b2_precision_informacion_motivo = S.b2_precision_informacion_motivo,
      T.b2_cobertura_temas_calif = S.b2_cobertura_temas_calif,
      T.b2_cobertura_temas_motivo = S.b2_cobertura_temas_motivo,
      T.b2_dominio_narrativa_resultado = S.b2_dominio_narrativa_resultado,
      T.b2_storytelling_calif = S.b2_storytelling_calif,
      T.b2_storytelling_motivo = S.b2_storytelling_motivo,
      T.b2_liderazgo_resultado = S.b2_liderazgo_resultado,
      T.b2_cumplimiento_duracion_estructura_calif = S.b2_cumplimiento_duracion_estructura_calif,
      T.b2_cumplimiento_duracion_estructura_motivo = S.b2_cumplimiento_duracion_estructura_motivo,
      T.b2_manejo_interrupciones_calif = S.b2_manejo_interrupciones_calif,
      T.b2_manejo_interrupciones_motivo = S.b2_manejo_interrupciones_motivo,
      T.b3_orientacion_servicio_resultado = S.b3_orientacion_servicio_resultado,
      T.b3_calidad_respuestas_calif = S.b3_calidad_respuestas_calif,
      T.b3_calidad_respuestas_motivo = S.b3_calidad_respuestas_motivo,
      T.b3_seguridad_responder_calif = S.b3_seguridad_responder_calif,
      T.b3_seguridad_responder_motivo = S.b3_seguridad_responder_motivo,
      T.b3_refuerzo_institucional_calif = S.b3_refuerzo_institucional_calif,
      T.b3_refuerzo_institucional_motivo = S.b3_refuerzo_institucional_motivo,
      T.load_date = S.load_date
  WHEN NOT MATCHED THEN
    INSERT (
      process_date,
      uri,
      audio,
      gcs_path,
      source_gcs_uri,
      folder_uuid,
      audio_id,
      firestore_path,
      mentor_id,
      mentor_nombre,
      mentor_email,
      mentor_campus,
      correo_grabador,
      activityid,
      colegio,
      codigo_modular,
      campus_evento,
      fecha_evento,
      tipo_charla_mentor,
      tipos_charla_mentor_json,
      prompt_name,
      prompt_sha256,
      estado_cruce,
      fecha_audio,
      duracion,
      json_text,
      final_json,
      full_response,
      status,
      puntaje_total,
      resumen_general,
      b1_comunicacion_oral_resultado,
      b1_claridad_discurso_calif,
      b1_claridad_discurso_motivo,
      b1_estructura_mensaje_calif,
      b1_estructura_mensaje_motivo,
      b1_palabras_clave_calif,
      b1_palabras_clave_motivo,
      b1_generacion_impacto_resultado,
      b1_mensajes_motivacionales_calif,
      b1_mensajes_motivacionales_motivo,
      b1_cierre_estructurado_calif,
      b1_cierre_estructurado_motivo,
      b1_conexion_engagement_resultado,
      b1_empatia_cercania_calif,
      b1_empatia_cercania_motivo,
      b1_interaccion_activacion_publico_calif,
      b1_interaccion_activacion_publico_motivo,
      b2_conocimiento_institucional_resultado,
      b2_precision_informacion_calif,
      b2_precision_informacion_motivo,
      b2_cobertura_temas_calif,
      b2_cobertura_temas_motivo,
      b2_dominio_narrativa_resultado,
      b2_storytelling_calif,
      b2_storytelling_motivo,
      b2_liderazgo_resultado,
      b2_cumplimiento_duracion_estructura_calif,
      b2_cumplimiento_duracion_estructura_motivo,
      b2_manejo_interrupciones_calif,
      b2_manejo_interrupciones_motivo,
      b3_orientacion_servicio_resultado,
      b3_calidad_respuestas_calif,
      b3_calidad_respuestas_motivo,
      b3_seguridad_responder_calif,
      b3_seguridad_responder_motivo,
      b3_refuerzo_institucional_calif,
      b3_refuerzo_institucional_motivo,
      load_date
    )
    VALUES (
      S.process_date,
      S.uri,
      S.audio,
      S.gcs_path,
      S.source_gcs_uri,
      S.folder_uuid,
      S.audio_id,
      S.firestore_path,
      S.mentor_id,
      S.mentor_nombre,
      S.mentor_email,
      S.mentor_campus,
      S.correo_grabador,
      S.activityid,
      S.colegio,
      S.codigo_modular,
      S.campus_evento,
      S.fecha_evento,
      S.tipo_charla_mentor,
      S.tipos_charla_mentor_json,
      S.prompt_name,
      S.prompt_sha256,
      S.estado_cruce,
      S.fecha_audio,
      S.duracion,
      S.json_text,
      S.final_json,
      S.full_response,
      S.status,
      S.puntaje_total,
      S.resumen_general,
      S.b1_comunicacion_oral_resultado,
      S.b1_claridad_discurso_calif,
      S.b1_claridad_discurso_motivo,
      S.b1_estructura_mensaje_calif,
      S.b1_estructura_mensaje_motivo,
      S.b1_palabras_clave_calif,
      S.b1_palabras_clave_motivo,
      S.b1_generacion_impacto_resultado,
      S.b1_mensajes_motivacionales_calif,
      S.b1_mensajes_motivacionales_motivo,
      S.b1_cierre_estructurado_calif,
      S.b1_cierre_estructurado_motivo,
      S.b1_conexion_engagement_resultado,
      S.b1_empatia_cercania_calif,
      S.b1_empatia_cercania_motivo,
      S.b1_interaccion_activacion_publico_calif,
      S.b1_interaccion_activacion_publico_motivo,
      S.b2_conocimiento_institucional_resultado,
      S.b2_precision_informacion_calif,
      S.b2_precision_informacion_motivo,
      S.b2_cobertura_temas_calif,
      S.b2_cobertura_temas_motivo,
      S.b2_dominio_narrativa_resultado,
      S.b2_storytelling_calif,
      S.b2_storytelling_motivo,
      S.b2_liderazgo_resultado,
      S.b2_cumplimiento_duracion_estructura_calif,
      S.b2_cumplimiento_duracion_estructura_motivo,
      S.b2_manejo_interrupciones_calif,
      S.b2_manejo_interrupciones_motivo,
      S.b3_orientacion_servicio_resultado,
      S.b3_calidad_respuestas_calif,
      S.b3_calidad_respuestas_motivo,
      S.b3_seguridad_responder_calif,
      S.b3_seguridad_responder_motivo,
      S.b3_refuerzo_institucional_calif,
      S.b3_refuerzo_institucional_motivo,
      S.load_date
    );

  UPDATE
    `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` AS control
  SET
    estado_proceso = 'CARGADO',
    motivo_estado = NULL,
    json_error_detalle = NULL,
    fecha_evaluacion = CURRENT_TIMESTAMP(),
    fecha_actualizacion = CURRENT_TIMESTAMP()
  WHERE EXISTS (
    SELECT 1
    FROM
      `_SESSION.tmp_mentores_json` AS evaluado
    WHERE evaluado.uri = control.uri AND evaluado.json_valido
      AND evaluado.prompt_name = control.prompt_name
      AND evaluado.prompt_sha256 = control.prompt_sha256
  );

  UPDATE
    `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` AS control
  SET
    estado_proceso = 'ERROR',
    motivo_estado = COALESCE(resultado.json_error_tipo, 'IA_ERROR'),
    json_error_detalle = LEFT(
      COALESCE(
        TO_JSON_STRING(resultado.full_response),
        resultado.clean_json_text,
        'SIN_RESPUESTA'
      ),
      4000
    ),
    fecha_actualizacion = CURRENT_TIMESTAMP()
  FROM (
    SELECT * EXCEPT(rn)
    FROM (
      SELECT
        json.*,
        ROW_NUMBER() OVER (
          PARTITION BY uri
          ORDER BY file_name
        ) AS rn
      FROM
        `_SESSION.tmp_mentores_json`
          AS json
      WHERE NOT json_valido
    )
    WHERE rn = 1
  ) AS resultado
  WHERE control.uri = resultado.uri
    AND NOT resultado.json_valido;

  INSERT INTO `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_control_ejecucion`
  SELECT p_run_id, CURRENT_TIMESTAMP(), c.*
  FROM `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control` c
  WHERE c.uri IN (SELECT uri FROM `_SESSION.tmp_mentores_control_universo`);
  COMMIT TRANSACTION;

  SELECT
    '01_parametros' AS control,
    CAST(v_fecha_proceso AS STRING) AS fecha_proceso,
    v_prompt_laboratorio AS prompt_laboratorio,
    v_prompt_auditorio AS prompt_auditorio,
    v_reprocesar_fecha AS reprocesar_fecha;

  SELECT
    '02_control_estados' AS control,
    estado_proceso,
    COUNT(*) AS cantidad
  FROM
    `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control`
  GROUP BY
    estado_proceso
  ORDER BY
    estado_proceso;

  SELECT
    '03_ejecucion_gemini' AS control,
    v_cantidad_audios_control AS audios_revisados_control,
    v_cantidad_audios_gemini AS audios_enviados_gemini,
    v_cantidad_resultados AS respuestas_ia,
    v_cantidad_respuestas_error AS respuestas_error,
    v_cantidad_resultados - v_cantidad_respuestas_error AS respuestas_ok;

  SELECT
    '04_pendientes_por_motivo' AS control,
    estado_proceso,
    motivo_estado,
    COUNT(*) AS cantidad
  FROM
    `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control`
  WHERE estado_proceso IN ('PENDIENTE', 'ERROR')
  GROUP BY
    estado_proceso,
    motivo_estado
  ORDER BY
    cantidad DESC;

  SELECT
    '05_errores_ia_detalle' AS control,
    uri,
    file_name AS audio,
    motivo_estado,
    json_error_detalle,
    fecha_actualizacion
  FROM
    `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_audio_control`
  WHERE estado_proceso = 'ERROR'
  ORDER BY
    fecha_actualizacion DESC
  LIMIT 100;

  SELECT
    '06_resultados_finales' AS control,
    process_date,
    tipo_charla_mentor, prompt_name,
    COUNT(*) AS cantidad,
    AVG(puntaje_total) AS puntaje_promedio
  FROM
    `prd-utpbi-data-operation.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd`
  WHERE process_date = v_fecha_proceso
  GROUP BY
    process_date, tipo_charla_mentor, prompt_name;

-- =====================================================================================
-- 7. ELIMINAR TABLAS TEMPORALES
-- =====================================================================================

DROP TABLE IF EXISTS
  `_SESSION.tmp_mentores_json`;

DROP TABLE IF EXISTS
  `_SESSION.tmp_mentores_ia_results`;

DROP TABLE IF EXISTS
  `_SESSION.tmp_mentores_audios_prompt`;

DROP TABLE IF EXISTS
  `prd-utpbi-data-operation.raw_cita_promotor.tmp_mentores_external`;

DROP TABLE IF EXISTS
  `_SESSION.tmp_mentores_audios_gemini`;

DROP TABLE IF EXISTS
  `_SESSION.tmp_mentores_control_universo`;

END;
