"""Catálogo Counter: un archivo por tarea; nunca concatena segmentos."""
import hashlib
import json
import os
import re
from datetime import date
from counter_common.contract import AudioRequest, parse_gcs_uri
from worker.config import ModelConfig, boolean
from worker.pipeline import processing_key
from worker.storage import GCSRepository


def table_id(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Tabla inválida: project.dataset.table")
    return value


def metadata(row):
    values = {k: str(v) if v is not None else None for k, v in dict(row).items()}
    original = values.get("s3_uri") or values["gcs_uri"]
    values["original_recording_id"] = hashlib.sha256(original.encode()).hexdigest()[:24]
    match = re.search(r"_s(\d+)\.[^.]+$", values.get("file_name") or "", re.I)
    values["segment_index"] = int(match.group(1)) if match else None
    values["speaker_scope"] = "segment"
    values["timestamp_scope"] = "segment"
    return values


def prepare(storage_client, bq_client):
    from google.cloud import bigquery
    source_table = table_id(os.environ["BQ_SOURCE_TABLE"])
    start, end = os.environ.get("PROCESS_DAY_FROM"), os.environ.get("PROCESS_DAY_TO")
    if start: date.fromisoformat(start)
    if end: date.fromisoformat(end)
    if start and end and start > end:
        raise ValueError("Rango de fechas invertido")
    limit = int(os.environ.get("BATCH_MAX_FILES", "100"))
    if not 1 <= limit <= 10000:
        raise ValueError("BATCH_MAX_FILES debe estar entre 1 y 10000")
    filters, params = ["gcs_uri IS NOT NULL"], []
    for value, op, name in [(start, ">=", "day_from"), (end, "<=", "day_to")]:
        if value:
            filters.append(f"process_day {op} @{name}")
            params.append(bigquery.ScalarQueryParameter(name, "DATE", value))
    sql = f"""SELECT * FROM `{source_table}` WHERE {' AND '.join(filters)}
        QUALIFY ROW_NUMBER() OVER(PARTITION BY gcs_uri ORDER BY load_date DESC, fecha_procesamiento DESC)=1
        ORDER BY process_day, gcs_uri"""
    rows = bq_client.query(sql, location=os.environ["BQ_SOURCE_LOCATION"],
                          job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    config, items = ModelConfig.from_env(), []
    allowed = tuple(b.strip() for b in os.environ["ALLOWED_INPUT_BUCKETS"].split(",") if b.strip())
    if not allowed:
        raise ValueError("ALLOWED_INPUT_BUCKETS requerido")
    include = boolean(os.environ.get("INCLUDE_COMPLETED", "false"))
    for row in rows:
        entry = metadata(row)
        entry["source_table"] = source_table
        request = AudioRequest.from_dict({"audio_uri": entry["gcs_uri"]}, allowed)
        repo = GCSRepository(storage_client, request.audio_uri, os.environ["OUTPUT_GCS_ROOT"],
                             int(os.environ.get("MAX_AUDIO_BYTES", "524288000")))
        key = processing_key(request, repo.source, config)
        if not include and repo.read_json(key, "completed.json"):
            continue
        items.append({"audio_uri": request.audio_uri, "recording_id": request.recording_id,
                      "catalog": entry, "source": repo.source})
        if len(items) == limit:
            break
    bucket, prefix = parse_gcs_uri(os.environ["MANIFEST_GCS_ROOT"].rstrip("/") + "/")
    batch_id = os.environ["BATCH_ID"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch_id):
        raise ValueError("BATCH_ID inválido")
    name = f"{prefix.rstrip('/')}/{batch_id}.json"
    payload = {"count": len(items), "source_table": source_table, "models": config.as_dict(), "items": items}
    storage_client.bucket(bucket).blob(name).upload_from_string(
        json.dumps(payload, ensure_ascii=False), content_type="application/json", if_generation_match=0)
    storage_client.bucket(bucket).blob(name + ".meta.json").upload_from_string(
        json.dumps({"count": len(items)}), content_type="application/json", if_generation_match=0)
    print(json.dumps({"manifest_uri": f"gs://{bucket}/{name}", "count": len(items)}))


def read_item(client):
    bucket, name = parse_gcs_uri(os.environ["MANIFEST_URI"])
    expected_bucket, expected_prefix = parse_gcs_uri(os.environ["MANIFEST_GCS_ROOT"].rstrip("/") + "/")
    if bucket != expected_bucket or not name.startswith(expected_prefix):
        raise ValueError("Manifiesto fuera del prefijo autorizado")
    manifest = json.loads(client.bucket(bucket).blob(name).download_as_text())
    if manifest["models"] != ModelConfig.from_env().as_dict():
        raise ValueError("Configuración de modelos cambió desde prepare; generar otro manifiesto")
    index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")) != manifest["count"]:
        raise ValueError("taskCount debe coincidir con el manifiesto")
    return manifest["items"][index]
