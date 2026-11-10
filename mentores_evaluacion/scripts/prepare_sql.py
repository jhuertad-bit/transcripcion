"""Genera la variante productiva. Solo escribe archivos locales; nunca ejecuta SQL."""
from pathlib import Path
import argparse
import ast
import re

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT.parents[1] / "02_Desarrollo"
PROJECT = "prd-utpbi-data-operation"
RAW = f"{PROJECT}.raw_cita_promotor"
FINAL = f"{PROJECT}.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd"
PROCEDURE = f"{RAW}.sp_process_speech_analytics_mentores"


def harden_response_contract(sql):
    """Exige los 39 campos y rechaza subatributos ausentes sin inventar notas."""
    match = re.search(r"'''(?P<schema>[\s\S]*?)''' AS output_schema", sql)
    if match is None:
        raise ValueError("No se encontro output_schema")
    fields = re.findall(r"^[ \t]*([a-z0-9_]+)[ \t]+(?:NOT NULL[ \t]+)?(FLOAT64|STRING)(?:[ \t]+NOT NULL)?[ \t]*,?[ \t]*$",
                        match.group("schema"), re.M)
    if len(fields) != 39:
        raise ValueError(f"Se esperaban 39 columnas; se encontraron {len(fields)}")
    schema = re.sub(r"^([ \t]*[a-z0-9_]+)[ \t]+(?:NOT NULL[ \t]+)?(FLOAT64|STRING)(?:[ \t]+NOT NULL)?([ \t]*,?[ \t]*)$",
                    r"\1 \2 NOT NULL\3", match.group("schema"), flags=re.M)
    sql = sql[:match.start("schema")] + schema + sql[match.end("schema"):]
    if "CREATE TEMP FUNCTION subatributos_completos_mentor(" in sql:
        return sql
    names = {name for name, _ in fields}
    pairs = [(name, name[:-len("_calif")] + "_motivo") for name, _ in fields if name.endswith("_calif")]
    if len(pairs) != 15 or any(motivo not in names for _, motivo in pairs):
        raise ValueError("Se requieren 15 calificaciones con sus motivos")
    function = """-- Valida evidencia completa antes de publicar; NULL no se convierte en 0 ni NA.
CREATE TEMP FUNCTION subatributos_completos_mentor(
  detalles ARRAY<STRUCT<calificacion STRING, motivo STRING>>
)
RETURNS BOOL
AS (
  ARRAY_LENGTH(detalles) = 15
  AND NOT EXISTS (
    SELECT 1 FROM UNNEST(detalles) AS detalle
    WHERE detalle.calificacion IS NULL
      OR UPPER(TRIM(detalle.calificacion)) NOT IN ('1', '0', 'NA')
      OR NULLIF(TRIM(detalle.motivo), '') IS NULL
  )
);

"""
    anchor = "CREATE TEMP TABLE tmp_mentores_control_universo"
    if sql.count(anchor) != 1:
        raise ValueError("No se encontro el inicio del proceso")
    sql = sql.replace(anchor, function + anchor, 1)
    rows = ",\n".join(f"          STRUCT({calif} AS calificacion, {motivo} AS motivo)" for calif, motivo in pairs)
    check = "subatributos_completos_mentor([\n" + rows + "\n        ])"
    valid_anchor = "        AND puntaje_total IS NOT NULL"
    error_anchor = "        WHEN puntaje_total IS NULL"
    if sql.count(valid_anchor) != 1 or sql.count(error_anchor) != 1:
        raise ValueError("No se encontraron las validaciones de respuesta")
    sql = sql.replace(valid_anchor, "        AND " + check + "\n" + valid_anchor, 1)
    sql = sql.replace(error_anchor, "        WHEN NOT " + check + " THEN 'RESPUESTA_INCOMPLETA'\n" + error_anchor, 1)
    return sql


def generate(refresh_prompts=False):
    ddl = (DEV / "01_test_manual_ddl_mentores_gen_ia.sql").read_text(encoding="utf-8")
    ddl = ddl[re.search(r"CREATE (?:OR REPLACE TABLE|TABLE IF NOT EXISTS)", ddl).start():]
    ddl = ddl.replace("CREATE OR REPLACE TABLE", "CREATE TABLE IF NOT EXISTS")
    ddl = ddl.replace(f"{PROJECT}.raw_genesys_audios", RAW)
    ddl = ddl[:ddl.index("-- La carga de control")]
    ddl += f"""
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_evaluaciones`
PARTITION BY DATE(evaluated_at) CLUSTER BY uri AS
SELECT CAST(NULL AS STRING) AS run_id, CURRENT_TIMESTAMP() AS evaluated_at, f.*
FROM `{FINAL}` f WHERE FALSE;
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_control_ejecucion`
PARTITION BY DATE(recorded_at) CLUSTER BY uri AS
SELECT CAST(NULL AS STRING) AS run_id, CURRENT_TIMESTAMP() AS recorded_at, c.*
FROM `{RAW}.hist_mentores_audio_control` c WHERE FALSE;
CREATE TABLE IF NOT EXISTS `{RAW}.mentores_pipeline_lock`
(lock_id STRING, owner STRING, expires_at TIMESTAMP);
MERGE `{RAW}.mentores_pipeline_lock` t
USING (SELECT 'pipeline' AS lock_id) s ON t.lock_id = s.lock_id
WHEN NOT MATCHED THEN INSERT (lock_id) VALUES (s.lock_id);
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_pipeline_runs`
(run_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP, status STRING,
 date_from DATE, date_to DATE, reprocess BOOL, error_detail STRING)
PARTITION BY DATE(started_at);
ALTER TABLE `{RAW}.hist_mentores_pipeline_runs` ADD COLUMN IF NOT EXISTS catalog_load_id STRING;
ALTER TABLE `{RAW}.hist_mentores_pipeline_runs` ADD COLUMN IF NOT EXISTS catalog_loaded_at TIMESTAMP;
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_ia_respuestas`
(run_id STRING, evaluated_at TIMESTAMP, uri STRING, full_response JSON,
 clean_json_text STRING, final_json JSON, json_valido BOOL, json_error_tipo STRING)
PARTITION BY DATE(evaluated_at) CLUSTER BY uri;
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_prompt_versions`
(prompt_name STRING, prompt_sha256 STRING, prompt_text STRING, registered_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{RAW}.hist_mentores_execution_plan`
(execution_id STRING, process_date DATE, catalog_load_id STRING, created_at TIMESTAMP)
PARTITION BY process_date CLUSTER BY execution_id;
ALTER TABLE `{RAW}.hist_mentores_execution_plan` ADD COLUMN IF NOT EXISTS catalog_load_id STRING;
"""
    s = (DEV / "02_test_manual_mentores_control_reproceso_gen_ia.sql").read_text(encoding="utf-8")
    s = s[s.index("DECLARE v_fecha_proceso"):]
    s = re.sub(r"DECLARE v_fecha_proceso DATE DEFAULT DATE '[^']+';",
               "DECLARE v_fecha_proceso DATE DEFAULT @date_to;", s)
    s = s.replace("DECLARE v_reprocesar_fecha BOOL DEFAULT FALSE;",
                  "DECLARE v_reprocesar_fecha BOOL DEFAULT @reprocess;")
    s = s.replace(f"{PROJECT}.raw_genesys_audios", RAW)
    s = s.replace(f"{PROJECT}.adf_speech_analytics.gemini-2-5-flash",
                  f"{PROJECT}.adf_speech_analytics.gemini-3-5-flash")
    s = s.replace("component:manual_audio_analyzer", "component:mentores_pipeline")
    s = s.replace("-- Carga diaria: toma audios de v_fecha_proceso y pendientes/errores anteriores.",
                  "-- Carga diaria: descubre audios disponibles hasta ayer, incluidos ingresos tardios.")
    s = s.replace("-- Reproceso: si v_reprocesar_fecha = TRUE, vuelve a evaluar todos los audios de la fecha.",
                  "-- Reproceso: evalua nuevamente los audios del rango inclusivo solicitado.")
    start = s.index("    AND (\n      DATE(fecha_audio) = v_fecha_proceso")
    end = s.index("\n  QUALIFY ROW_NUMBER()", start)
    s = s[:start] + """    AND DATE(fecha_audio) <= @date_to
    AND (NOT @reprocess OR DATE(fecha_audio) >= @date_from)
""" + s[end:]
    s = s.replace("WHEN v_reprocesar_fecha AND p.fecha_audio = v_fecha_proceso THEN",
                  "WHEN v_reprocesar_fecha AND p.fecha_audio BETWEEN @date_from AND @date_to THEN")
    # La seleccion parte del universo validado de esta ejecucion, tambien en reprocesos.
    s = s.replace("WHERE estado_proceso IN ('PENDIENTE', 'ERROR')\n  AND",
                  f"WHERE uri IN (SELECT uri FROM `{RAW}.tmp_mentores_control_universo`)\n"
                  "  AND estado_proceso IN ('PENDIENTE', 'ERROR')\n  AND", 1)
    merge_start = s.index("  MERGE\n    `" + FINAL)
    source_start = s.index("  USING (", merge_start) + len("  USING (")
    source_end = s.index("\n  ) AS S\n  ON T.uri", source_start)
    result_source = s[source_start:source_end]
    archive = f"""  BEGIN TRANSACTION;
  INSERT INTO `{RAW}.hist_mentores_ia_respuestas`
  SELECT @run_id, CURRENT_TIMESTAMP(), uri, full_response, clean_json_text,
         final_json, json_valido, json_error_tipo
  FROM `{RAW}.tmp_mentores_json`;
  INSERT INTO `{RAW}.hist_mentores_evaluaciones`
  SELECT @run_id AS run_id, CURRENT_TIMESTAMP() AS evaluated_at, result.*
  FROM ({result_source}\n  ) result;
"""
    s = s[:merge_start] + archive + s[merge_start:]
    marker = "  SELECT\n    '01_parametros'"
    s = s.replace(marker, f"""  INSERT INTO `{RAW}.hist_mentores_control_ejecucion`
  SELECT @run_id, CURRENT_TIMESTAMP(), c.*
  FROM `{RAW}.hist_mentores_audio_control` c
  WHERE c.uri IN (SELECT uri FROM `{RAW}.tmp_mentores_control_universo`);
  COMMIT TRANSACTION;

""" + marker)
    # Tablas de trabajo distintas por ejecucion, con vencimiento por si falla el proceso.
    s = re.sub(r"tmp_mentores_([a-z_]+)", r"tmp_mentores_\1__RUN_SUFFIX__", s)
    s = re.sub(r"(CREATE OR REPLACE TABLE\s+`[^`]+`)\s+AS",
               r"\1 OPTIONS(expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 2 DAY))\nAS", s)
    s = s.replace("object_metadata = 'SIMPLE',",
                  "expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 2 DAY),\n        object_metadata = 'SIMPLE',")
    sql = ROOT / "sql"
    sql.mkdir(exist_ok=True)
    (sql / "01_init.sql").write_text(ddl, encoding="utf-8")
    # El procedimiento usa tablas TEMP de la sesion; solo el objeto externo es compartido.
    # El bloqueo adquirido por el runner es obligatorio antes de llamar al SP.
    body = s.replace("__RUN_SUFFIX__", "")
    for parameter in ("date_from", "date_to", "reprocess", "run_id"):
        body = body.replace("@" + parameter, "p_" + parameter)
    body = body.replace("AND (NOT p_reprocess OR DATE(fecha_audio) >= p_date_from)",
                        "AND DATE(fecha_audio) >= p_date_from")
    # Usar solo catalogos vigentes; el identificador detecta cambios entre fechas del rango.
    catalogs = {"maestro_audios_mentores_firestore": "cat_audios_mentores_firestore",
                "maestro_mentores_firestore": "cat_mentores_firestore", "dias_utp": "cat_dias_utp"}
    for table, target in catalogs.items():
        source = f"FROM `{RAW}.{table}`"
        body = body.replace(source, f"FROM `{RAW}.{target}`")
    body = body.replace("WHERE source_gcs_uri IS NOT NULL", "WHERE catalog_load_id=p_catalog_load_id AND source_gcs_uri IS NOT NULL")
    body = body.replace(f"FROM `{RAW}.cat_mentores_firestore`\n  GROUP BY", f"FROM `{RAW}.cat_mentores_firestore`\n  WHERE catalog_load_id=p_catalog_load_id\n  GROUP BY")
    body = body.replace(f"FROM `{RAW}.cat_dias_utp`\n  GROUP BY", f"FROM `{RAW}.cat_dias_utp`\n  WHERE catalog_load_id=p_catalog_load_id\n  GROUP BY")
    work_names = set(re.findall(r"tmp_mentores_[a-z_]+", body)) - {"tmp_mentores_external"}
    for name in sorted(work_names, key=len, reverse=True):
        body = body.replace(f"`{RAW}.{name}`", f"`_SESSION.{name}`")
    body = re.sub(r"CREATE OR REPLACE TABLE\s+`_SESSION\.([a-z_]+)`\s+OPTIONS\(expiration_timestamp=TIMESTAMP_ADD\(CURRENT_TIMESTAMP\(\), INTERVAL 2 DAY\)\)",
                  r"CREATE TEMP TABLE \1", body)
    guard = f"""ASSERT p_date_from IS NOT NULL AND p_date_to IS NOT NULL
  AND p_date_from = p_date_to AND p_reprocess IS NOT NULL AS 'PARAMETROS_INVALIDOS_UN_DIA_POR_LLAMADA';
ASSERT REGEXP_CONTAINS(p_run_id, r'^[a-f0-9]{{32}}$') AS 'RUN_ID_INVALIDO';
ASSERT (SELECT COUNT(*) = 1 FROM `{RAW}.mentores_pipeline_lock`
  WHERE lock_id='pipeline' AND owner=p_lock_owner
    AND STARTS_WITH(owner, CONCAT(p_run_id, '_')) AND expires_at>CURRENT_TIMESTAMP())
  AS 'EJECUTAR_DESDE_WORKFLOW_CON_BLOQUEO';

"""
    for target in catalogs.values():
        guard += f"""ASSERT (SELECT COUNT(*)>0 AND COUNT(DISTINCT catalog_load_id)=1
  AND MIN(catalog_load_id)=p_catalog_load_id FROM `{RAW}.{target}`)
  AS 'CATALOGO_CAMBIO_REINICIAR_WORKFLOW';
"""
    body = body.replace('SET @@query_label =', guard + 'SET @@query_label =', 1)
    procedure = f"""-- Desplegado por Cloud Build durante init. No evalua audios al crear el SP.
-- El Job llama al SP despues de publicar catalogos y adquirir el bloqueo.
CREATE OR REPLACE PROCEDURE `{PROCEDURE}`(
  IN p_date_from DATE,
  IN p_date_to DATE,
  IN p_reprocess BOOL,
  IN p_run_id STRING,
  IN p_lock_owner STRING,
  IN p_catalog_load_id STRING
)
OPTIONS(strict_mode=FALSE, description='Speech Analytics Mentores: control, evaluacion IA, historico y publicacion final. Invocar mediante el Workflow.')
BEGIN
{body}
END;
"""
    (sql / "02_sp_process.sql").write_text(harden_response_contract(procedure), encoding="utf-8")
    for source, target in (("Mentores_Prompt_Laboratorio.md", "laboratorio.md"),
                           ("Mentores_Prompt_Auditorio.md", "auditorio.md")):
        if refresh_prompts or not (sql / target).exists():
            (sql / target).write_bytes((DEV / source).read_bytes())
    # Reutilizar exclusivamente normalizacion y esquemas, sin codigo de autenticacion local.
    connections = DEV.parent / "00_Conexiones"
    maestro = ast.parse((connections / "cargar_maestros_mentores.py").read_text(encoding="utf-8"))
    dias = ast.parse((connections / "descargar_dias_utp.py").read_text(encoding="utf-8"))
    decoder = ast.parse((connections / "consultar_colecciones.py").read_text(encoding="utf-8"))
    selected = [n for n in maestro.body if isinstance(n, ast.FunctionDef) and n.name in
                ("texto", "fecha", "ruta_storage", "transformar")]
    selected += [n for n in decoder.body if isinstance(n, ast.FunctionDef) and n.name == "decodificar"]
    selected += [n for n in dias.body if isinstance(n, ast.FunctionDef) and n.name == "transformar_datos"]
    schemas = next(n for n in maestro.body if isinstance(n, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id == "ESQUEMAS" for t in n.targets))
    loader = next(n for n in dias.body if isinstance(n, ast.FunctionDef) and n.name == "cargar_a_bigquery")
    event_schema = next(n for n in loader.body if isinstance(n, ast.Assign) and
                        any(isinstance(t, ast.Name) and t.id == "schema" for t in n.targets))
    adapter = ('# Generado desde normalizadores locales; sin credenciales ni conexiones.\n'
               'import json\nimport pandas as pd\nfrom datetime import datetime, timezone\n'
               'from urllib.parse import unquote, urlparse\nfrom google.cloud import bigquery\n\n')
    adapter += ast.unparse(schemas) + '\nDIAS_SCHEMA = ' + ast.unparse(event_schema.value) + '\n\n'
    adapter += '\n\n'.join(ast.unparse(n) for n in selected)
    adapter = adapter.replace('    from consultar_colecciones import decodificar\n', '')
    (ROOT / "worker" / "normalizers.py").write_text(adapter + '\n', encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-prompts", action="store_true",
                        help="Reemplazar los prompts del paquete desde los Markdown de desarrollo")
    generate(refresh_prompts=parser.parse_args().refresh_prompts)
