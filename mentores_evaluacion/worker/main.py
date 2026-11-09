"""Runner para Cloud Run Job. Las credenciales se resuelven mediante ADC."""
import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

PROJECT = "prd-utpbi-data-operation"
RAW = f"{PROJECT}.raw_cita_promotor"
SQL = Path(__file__).resolve().parents[1] / "sql"
PROCEDURE = f"{RAW}.sp_process_speech_analytics_mentores"
PROCESS_CALL = f"CALL `{PROCEDURE}`(@date_from, @date_to, @reprocess, @run_id, @owner, @catalog_load_id);"


def parameters(env):
    start, end = env.get("DATE_FROM", ""), env.get("DATE_TO", "")
    flag = env.get("REPROCESS", "false").lower()
    if flag not in ("true", "false"):
        raise ValueError("REPROCESS debe ser true o false")
    if bool(start) != bool(end):
        raise ValueError("DATE_FROM y DATE_TO deben indicarse juntos")
    if flag == "true" and not start:
        raise ValueError("Un reproceso requiere DATE_FROM y DATE_TO")
    yesterday = datetime.now(ZoneInfo("America/Lima")).date() - timedelta(days=1)
    first, last = (date.fromisoformat(start), date.fromisoformat(end)) if start else (yesterday, yesterday)
    if first > last:
        raise ValueError("Rango de fechas invertido")
    token = env.get("RUN_ID") or env.get("CLOUD_RUN_EXECUTION") or str(uuid.uuid4())
    run = hashlib.sha256(token.encode()).hexdigest()[:32]
    return first, last, flag == "true", run


def main():
    from google.api_core.exceptions import Conflict
    from google.cloud import bigquery
    from worker import catalogs

    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=["init", "prepare", "run"], default="run")
    action = parser.parse_args().action
    first, last, reprocess, run_id = parameters(os.environ)
    if action == "run" and (first != last or not os.getenv("CATALOG_LOAD_ID")):
        raise ValueError("Ejecutar mediante Workflow: run requiere un solo dia y CATALOG_LOAD_ID")
    client = bigquery.Client(project=PROJECT, location=os.getenv("BQ_LOCATION", "US"))
    params = [bigquery.ScalarQueryParameter("date_from", "DATE", first),
              bigquery.ScalarQueryParameter("date_to", "DATE", last),
              bigquery.ScalarQueryParameter("reprocess", "BOOL", reprocess),
              bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    params.extend([bigquery.ScalarQueryParameter("catalog_load_id", "STRING", os.getenv("CATALOG_LOAD_ID") or run_id),
                   bigquery.ScalarQueryParameter("execution_id", "STRING", os.getenv("RUN_ID", run_id)),
                   bigquery.ScalarQueryParameter("explicit_range", "BOOL", bool(os.getenv("DATE_FROM")))])

    def query(sql, job_id=None):
        config = bigquery.QueryJobConfig(query_parameters=params, labels={"component": "mentores-prd"})
        config.job_timeout_ms = 6 * 60 * 60 * 1000
        try:
            job = client.query(sql, job_config=config, job_id=job_id)
        except Conflict:
            if not job_id:
                raise
            job = client.get_job(job_id)
        return list(job.result())

    if action == "init":
        for dataset in ("raw_cita_promotor", "adf_speech_analytics"):
            if client.get_dataset(f"{PROJECT}.{dataset}").location.upper() != client.location.upper():
                raise ValueError(f"Ubicacion incompatible: {dataset}")
        client.get_table(f"{RAW}.hist_cita_mentor_audio_catalog")
        client.get_model(f"{PROJECT}.adf_speech_analytics.gemini-2-5-flash")
        query((SQL / "01_init.sql").read_text(encoding="utf-8"))
        catalogs.initialize(client, RAW, query)
        # Los prompts se administran exclusivamente para mentores, sin tocar otros flujos.
        query(f"CREATE TABLE IF NOT EXISTS `{RAW}.sys_prompts` (prompt_name STRING, prompt_text STRING)")
        for name, file in (("prospeccion_mentores_laboratorio", "laboratorio.md"),
                           ("prospeccion_mentores_auditorio", "auditorio.md")):
            text = (SQL / file).read_text(encoding="utf-8")
            config = bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("name", "STRING", name),
                bigquery.ScalarQueryParameter("text", "STRING", text)])
            client.query(f"""BEGIN TRANSACTION;
MERGE `{RAW}.hist_mentores_prompt_versions` t
USING (SELECT @name AS prompt_name, LOWER(TO_HEX(SHA256(@text))) AS prompt_sha256, @text AS prompt_text) s
ON t.prompt_name=s.prompt_name AND t.prompt_sha256=s.prompt_sha256
WHEN NOT MATCHED THEN INSERT(prompt_name,prompt_sha256,prompt_text,registered_at)
VALUES(s.prompt_name,s.prompt_sha256,s.prompt_text,CURRENT_TIMESTAMP());
MERGE `{RAW}.sys_prompts` t USING (SELECT @name AS prompt_name, @text AS prompt_text) s
ON t.prompt_name=s.prompt_name WHEN MATCHED THEN UPDATE SET prompt_text=s.prompt_text
WHEN NOT MATCHED THEN INSERT (prompt_name,prompt_text) VALUES(s.prompt_name,s.prompt_text);
COMMIT TRANSACTION;""",
                         job_config=config).result()
        # Registrar el procedimiento persistente despues de crear sus dependencias.
        query((SQL / "02_sp_process.sql").read_text(encoding="utf-8"))
        print("Inicializacion y despliegue del SP completados; no se evaluaron audios")
        return

    owner = run_id + '_' + uuid.uuid4().hex
    params.append(bigquery.ScalarQueryParameter("owner", "STRING", owner))
    # Una unica fila existente impide carreras de INSERT entre ejecuciones concurrentes.
    query(f"""BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*)=1 FROM `{RAW}.mentores_pipeline_lock` WHERE lock_id='pipeline') AS 'LOCK_INVALIDO';
ASSERT (SELECT owner IS NULL OR expires_at<CURRENT_TIMESTAMP()
        FROM `{RAW}.mentores_pipeline_lock` WHERE lock_id='pipeline') AS 'PIPELINE_OCUPADO';
UPDATE `{RAW}.mentores_pipeline_lock` SET owner=@owner,
 expires_at=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 8 HOUR) WHERE lock_id='pipeline';
COMMIT TRANSACTION;""")
    try:
        if query(f"SELECT run_id FROM `{RAW}.hist_mentores_pipeline_runs` WHERE run_id=@run_id AND status='SUCCESS'"):
            print("Ejecucion ya completada: " + run_id)
            return
        query(f"""MERGE `{RAW}.hist_mentores_pipeline_runs` t USING (SELECT @run_id AS run_id) s
ON t.run_id=s.run_id WHEN NOT MATCHED THEN
INSERT(run_id,started_at,status,date_from,date_to,reprocess,catalog_load_id)
VALUES(@run_id,CURRENT_TIMESTAMP(),'RUNNING',@date_from,@date_to,@reprocess,@catalog_load_id)
WHEN MATCHED THEN UPDATE SET status='RUNNING', error_detail=NULL,catalog_load_id=@catalog_load_id;""")
        if action == "prepare":
            catalogs.publish(client, RAW, run_id, os.getenv("FIRESTORE_PROJECT", "citapromotor-utp"), query)
            query(plan_sql(), job_id="mentores_plan_" + run_id)
        else:
            query(PROCESS_CALL, job_id="mentores_eval_" + run_id)
        query(f"UPDATE `{RAW}.hist_mentores_pipeline_runs` SET status='SUCCESS',finished_at=CURRENT_TIMESTAMP() WHERE run_id=@run_id")
        print(json.dumps({"status": "SUCCESS", "run_id": run_id, "date_from": str(first), "date_to": str(last)}))
    except Exception as exc:
        params.append(bigquery.ScalarQueryParameter("error", "STRING", str(exc)[:4000]))
        query(f"UPDATE `{RAW}.hist_mentores_pipeline_runs` SET status='ERROR',finished_at=CURRENT_TIMESTAMP(),error_detail=@error WHERE run_id=@run_id")
        raise
    finally:
        query(f"UPDATE `{RAW}.mentores_pipeline_lock` SET owner=NULL, expires_at=NULL WHERE lock_id='pipeline' AND owner=@owner")
        client.close()


def plan_sql():
    final = f"{PROJECT}.adf_speech_analytics.hist_utp_gen_ia_results_process_data_mentores_prd"
    return f"""INSERT INTO `{RAW}.hist_mentores_execution_plan` (execution_id,process_date,catalog_load_id,created_at)
SELECT @execution_id, day, @run_id, CURRENT_TIMESTAMP()
FROM (
 SELECT day FROM UNNEST(GENERATE_DATE_ARRAY(@date_from,@date_to)) day WHERE @explicit_range
 UNION DISTINCT
 SELECT DATE(c.fecha_audio) AS day FROM `{RAW}.hist_cita_mentor_audio_catalog` c
 WHERE NOT @explicit_range AND DATE(c.fecha_audio)<=@date_to
   AND c.gcs_uri IS NOT NULL AND c.copy_result='copied'
   AND COALESCE(SAFE_CAST(c.duration_seconds AS FLOAT64),0)>=60
   AND (
     NOT EXISTS(SELECT 1 FROM `{final}` f WHERE f.uri=c.gcs_uri)
     OR EXISTS(SELECT 1 FROM `{RAW}.hist_mentores_audio_control` t
               WHERE t.uri=c.gcs_uri AND t.estado_proceso IN ('PENDIENTE','ERROR'))
     OR EXISTS(SELECT 1 FROM `{final}` f JOIN `{RAW}.sys_prompts` p USING(prompt_name)
               WHERE f.uri=c.gcs_uri AND f.prompt_sha256!=LOWER(TO_HEX(SHA256(p.prompt_text))))
   )
) WHERE NOT EXISTS(SELECT 1 FROM `{RAW}.hist_mentores_execution_plan` p
                   WHERE p.execution_id=@execution_id AND p.process_date=day);"""


if __name__ == "__main__":
    main()
