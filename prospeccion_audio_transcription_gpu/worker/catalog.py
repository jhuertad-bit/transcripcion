"""Catálogos de prospección: audios completos, separados por tipo de origen."""
import hashlib
import json
import os
import re
from datetime import date
from prospeccion_common.contract import AudioRequest, parse_gcs_uri
from worker.config import ModelConfig, boolean
from worker.pipeline import processing_key
from worker.storage import GCSRepository


def table_id(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Tabla inválida: project.dataset.table")
    return value


SOURCES = {
    "promotor": "prd-utpbi-data-operation.raw_cita_promotor.hist_cita_promotor_audio_catalog",
    "mentor": "prd-utpbi-data-operation.raw_cita_promotor.hist_cita_mentor_audio_catalog",
}


def origin(value):
    if value not in SOURCES:
        raise ValueError("TIPO_ORIGEN debe ser promotor o mentor")
    return value


def output_root(tipo):
    return os.environ["OUTPUT_GCS_ROOT"].rstrip("/") + "/" + origin(tipo)


def metadata(row, tipo=None):
    values = json.loads(json.dumps(dict(row), default=str))
    values["tipo_origen"] = origin(tipo or values["tipo_origen"])
    values["folder_uuid"] = str(values["folder_uuid"]) if values.get("folder_uuid") is not None else None
    values["recording_id"] = hashlib.sha256(
        ("prospeccion|" + values["tipo_origen"] + "|" + values["gcs_uri"]).encode()).hexdigest()[:24]
    values["speaker_scope"] = "recording"
    values["timestamp_scope"] = "recording"
    return values


def prepare(storage_client, bq_client):
    from google.cloud import bigquery
    tipo = origin(os.environ["TIPO_ORIGEN"])
    expected_table = os.environ.get(f"BQ_{tipo.upper()}_SOURCE_TABLE") or SOURCES[tipo]
    source_table = table_id(os.environ.get("BQ_SOURCE_TABLE") or expected_table)
    if source_table != expected_table:
        raise ValueError("BQ_SOURCE_TABLE no coincide con TIPO_ORIGEN")
    start, end = os.environ.get("PROCESS_DAY_FROM"), os.environ.get("PROCESS_DAY_TO")
    if start: date.fromisoformat(start)
    if end: date.fromisoformat(end)
    if start and end and start > end:
        raise ValueError("Rango de fechas invertido")
    limit = int(os.environ.get("BATCH_MAX_FILES", "100"))
    per_worker = int(os.environ.get("FILES_PER_WORKER", "5"))
    if not 1 <= per_worker <= 100:
        raise ValueError("FILES_PER_WORKER debe estar entre 1 y 100")
    if not 1 <= limit <= 10000:
        raise ValueError("BATCH_MAX_FILES debe estar entre 1 y 10000")
    if not start or not end:
        raise ValueError("PROCESS_DAY_FROM y PROCESS_DAY_TO son obligatorios")
    filters, params = ["gcs_uri IS NOT NULL", "copy_result IN ('copied', 'already_in_gcs')"], []
    for value, op, name in [(start, ">=", "day_from"), (end, "<=", "day_to")]:
        if value:
            filters.append(f"fecha_audio {op} @{name}")
            params.append(bigquery.ScalarQueryParameter(name, "DATE", value))
    sql = f"""SELECT * FROM `{source_table}` WHERE {' AND '.join(filters)}
        QUALIFY ROW_NUMBER() OVER(PARTITION BY gcs_uri ORDER BY fecha_procesamiento DESC)=1
        ORDER BY fecha_audio, gcs_uri"""
    rows = bq_client.query(sql, location=os.environ["BQ_SOURCE_LOCATION"],
                          job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    config, items = ModelConfig.from_env(), []
    allowed = tuple(b.strip() for b in os.environ["ALLOWED_INPUT_BUCKETS"].split(",") if b.strip())
    if not allowed:
        raise ValueError("ALLOWED_INPUT_BUCKETS requerido")
    include = boolean(os.environ.get("INCLUDE_COMPLETED", "false"))
    for row in rows:
        entry = metadata(row, tipo)
        entry["source_table"] = source_table
        request = AudioRequest.from_dict({"audio_uri": entry["gcs_uri"], "recording_id": entry["recording_id"]}, allowed)
        repo = GCSRepository(storage_client, request.audio_uri, output_root(tipo),
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
    name = f"{prefix.rstrip('/')}/{tipo}/{batch_id}.json"
    task_count = (len(items) + per_worker - 1) // per_worker
    payload = {"count": len(items), "task_count": task_count, "files_per_worker": per_worker,
               "tipo_origen": tipo, "source_table": source_table, "models": config.as_dict(), "items": items}
    storage_client.bucket(bucket).blob(name).upload_from_string(
        json.dumps(payload, ensure_ascii=False), content_type="application/json", if_generation_match=0)
    storage_client.bucket(bucket).blob(name + ".meta.json").upload_from_string(
        json.dumps({"count": len(items), "task_count": task_count}), content_type="application/json", if_generation_match=0)
    print(json.dumps({"manifest_uri": f"gs://{bucket}/{name}", "count": len(items)}))


def read_items(client):
    bucket, name = parse_gcs_uri(os.environ["MANIFEST_URI"])
    expected_bucket, expected_prefix = parse_gcs_uri(os.environ["MANIFEST_GCS_ROOT"].rstrip("/") + "/")
    if bucket != expected_bucket or not name.startswith(expected_prefix):
        raise ValueError("Manifiesto fuera del prefijo autorizado")
    manifest = json.loads(client.bucket(bucket).blob(name).download_as_text())
    if manifest["models"] != ModelConfig.from_env().as_dict():
        raise ValueError("Configuración de modelos cambió desde prepare; generar otro manifiesto")
    index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    per_worker = manifest.get("files_per_worker", 1)
    if not isinstance(per_worker, int) or not 1 <= per_worker <= 100:
        raise ValueError("files_per_worker inválido en manifiesto")
    expected_tasks = manifest.get("task_count", manifest["count"])
    if manifest["count"] != len(manifest["items"]) or expected_tasks != (len(manifest["items"]) + per_worker - 1) // per_worker:
        raise ValueError("Conteos inconsistentes en manifiesto")
    if int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")) != expected_tasks:
        raise ValueError("taskCount debe coincidir con el manifiesto")
    if not 0 <= index < expected_tasks:
        raise ValueError("Índice de tarea fuera de rango")
    return manifest["items"][index * per_worker:(index + 1) * per_worker]


def read_item(client):
    """Compatibilidad con consumidores del manifiesto de una URI por tarea."""
    items = read_items(client)
    if len(items) != 1:
        raise ValueError("Este manifiesto requiere read_items")
    return items[0]
