"""Three independently resumable jobs; JSON artifacts are the source of truth."""
from datetime import date
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from counter_presencial.shared import parse_gcs_uri
from counter_presencial.shared import table_id
from counter_presencial.acoustics import Models, process_audio
from counter_presencial.config import VERSION, digest
from counter_presencial.conversations import build_conversations
from counter_presencial.fusion import fuse, second_pass
from counter_presencial.prompts import PROMPT, PROMPT_VERSION, REDACTION_VERSION, SECOND_PASS
from counter_presencial.redaction import Redactor
from counter_presencial.roles import BigQueryGemini, classify, role_key, safe_turns


def read_uri(client, uri):
    bucket, path = parse_gcs_uri(uri)
    return json.loads(client.bucket(bucket).blob(path).download_as_text())


def dispatch_key(item, config):
    """Selection identity available before redaction/model execution."""
    return digest({"acoustic_key": item["acoustic_key"], "model": config.role_model,
                   "prompt": digest(PROMPT + SECOND_PASS), "prompt_version": PROMPT_VERSION,
                   "redaction": REDACTION_VERSION, "fusion": "presencial-fusion-v1",
                   "targets": config.targets_per_call, "review_budget": config.max_second_pass,
                   "sink": os.environ.get("PRESENCIAL_BQ_RESULTS_TABLE", "gcs-only")})


def mark_published(item, result, config, store):
    store.put("published/" + dispatch_key(item, config) + ".json",
              {"role_key": result["role_key"], "acoustic_key": item["acoustic_key"]})


def prepare(config, store, client, bq):
    from google.cloud import bigquery
    config.require_pinned_models()
    start, end = os.environ["PROCESS_DAY_FROM"], os.environ["PROCESS_DAY_TO"]
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Inverted date range")
    source = table_id(os.environ["BQ_SOURCE_TABLE"])
    from counter_presencial.isolation import Resources
    if source != Resources(config.project).table("catalog_vnext"):
        raise ValueError("Catalog must be in the selected project and dedicated dataset")
    sql = f"""SELECT * FROM `{source}`
      WHERE gcs_uri IS NOT NULL AND process_day BETWEEN @start AND @end
      QUALIFY ROW_NUMBER() OVER(PARTITION BY gcs_uri ORDER BY load_date DESC, fecha_procesamiento DESC)=1
      ORDER BY process_day, gcs_uri"""
    rows = bq.query(sql, location=os.environ.get("BQ_SOURCE_LOCATION", config.bq_location),
                    job_config=bigquery.QueryJobConfig(query_parameters=[
                        bigquery.ScalarQueryParameter("start", "DATE", start),
                        bigquery.ScalarQueryParameter("end", "DATE", end)])).result()
    mapping_uri = os.environ.get("PRESENCIAL_ORIGINALS_MAP_URI")
    originals = read_uri(client, mapping_uri) if mapping_uri else None
    conversations = build_conversations(rows, originals)
    ready, review = [], []
    for item in conversations:
        if item["status"] != "ready":
            if len(review) < config.max_conversations:
                review.append(item)
            continue
        pinned = []
        for part in item["parts"]:
            pinned.append({**part, **store.source(part["uri"], config.allowed_buckets, config.max_audio_bytes)})
        item["parts"] = pinned
        item["acoustic_key"] = digest({"conversation": item, "spec": config.acoustic_spec()})
        if store.read("published/" + dispatch_key(item, config) + ".json"):
            continue
        ready.append(item)
        if len(ready) >= config.max_conversations:
            break
    # Balance by source size until measured durations are available. Never split a conversation.
    count = (len(ready) + config.files_per_task - 1) // config.files_per_task
    shards, weights = [[] for _ in range(count)], [0] * count
    for item in sorted(ready, key=lambda x: -sum(p["size_bytes"] for p in x["parts"])):
        candidates = [i for i in range(count) if len(shards[i]) < config.files_per_task]
        index = min(candidates, key=lambda i: weights[i])
        shards[index].append(item)
        weights[index] += sum(p["size_bytes"] for p in item["parts"])
    batch = os.environ["BATCH_ID"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch):
        raise ValueError("Invalid batch ID")
    manifest = {"schema_version": "2.0", "pipeline_version": VERSION,
                "acoustic_spec": config.acoustic_spec(), "items": ready, "shards": shards,
                "count": len(ready), "task_count": count, "needs_source_review": review}
    uri = store.put("manifests/" + batch + ".json", manifest)
    store.put("manifests/" + batch + ".meta.json",
              {"count": len(ready), "task_count": count, "source_review_count": len(review)})
    print(json.dumps({"manifest_uri": uri, "count": len(ready), "needs_source_review": len(review)}))
    return manifest


def manifest_items(config, store, client, sharded=False):
    uri = os.environ["MANIFEST_URI"]
    bucket, path = parse_gcs_uri(uri)
    if bucket != store.bucket.name or not path.startswith(store.prefix + "manifests/"):
        raise ValueError("Manifest is outside the VNext output prefix")
    manifest = read_uri(client, uri)
    if manifest.get("acoustic_spec") != config.acoustic_spec() or manifest.get("schema_version") != "2.0":
        raise ValueError("Manifest version/model configuration changed")
    if manifest["count"] != len(manifest["items"]):
        raise ValueError("Inconsistent manifest count")
    flat = [x for shard in manifest["shards"] for x in shard]
    if (len(flat) != len(manifest["items"]) or manifest["task_count"] != len(manifest["shards"])
            or sorted(digest(x) for x in flat) != sorted(digest(x) for x in manifest["items"])):
        raise ValueError("Manifest sharding does not exactly partition conversations")
    for item in manifest["items"]:
        recorded = item["acoustic_key"]
        conversation = {k: v for k, v in item.items() if k != "acoustic_key"}
        if recorded != digest({"conversation": conversation, "spec": config.acoustic_spec()}):
            raise ValueError("Manifest acoustic key does not match its sources")
        for part in item["parts"]:
            bucket, _ = parse_gcs_uri(part["uri"])
            if bucket not in config.allowed_buckets or not str(part["generation"]).isdigit():
                raise ValueError("Unauthorized or unpinned source")
    if not sharded:
        return manifest["items"]
    index, count = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0")), int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    if count != manifest["task_count"] or not 0 <= index < count:
        raise ValueError("Cloud Run task count/index differs from manifest")
    return manifest["shards"][index]


def completed(store, stage, key):
    pointer = store.read(f"{stage}/{key}/completed.json")
    if pointer is None:
        return None
    path = pointer.get("path", "")
    if not path.startswith(f"{stage}/{key}/attempts/") or ".." in path.split("/"):
        raise ValueError("Invalid completed artifact path")
    result = store.read(path)
    if result is None or pointer.get("sha256") != digest(result):
        raise ValueError("Completed artifact integrity failure")
    return result


def publish_stage(store, stage, key, result, attempt, check):
    path = f"{stage}/{key}/attempts/{attempt}/result.json"
    check()
    store.put(path, result)
    if stage == "roles":
        check()
        store.put_text(f"{stage}/{key}/attempts/{attempt}/transcript.txt", result["serialized_level_1"])
    check()
    store.put(f"{stage}/{key}/completed.json", {"path": path, "sha256": digest(result)})


def run_acoustic(item, config, store, models):
    key = item["acoustic_key"]
    with store.lease("acoustic-" + key) as check:
        previous = completed(store, "acoustic", key)
        if previous is not None:
            return previous
        attempt = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="counter-presencial-") as folder:
            result = process_audio(item, config, store, models, Path(folder))
        result.update(acoustic_key=key, attempt_id=attempt)
        publish_stage(store, "acoustic", key, result, attempt, check)
        return result


def run_roles(item, acoustic, config, store, provider, redactor):
    turns = acoustic["transcription"]
    safe = safe_turns(turns, redactor)
    key = role_key(item["acoustic_key"], safe, config.role_model, config)
    with store.lease("roles-" + key) as check:
        previous = completed(store, "roles", key)
        if previous is not None:
            return previous
        attempt, started = uuid.uuid4().hex, time.monotonic()

        def checkpoint(action, response_key, answer):
            name = f"roles/{key}/responses/{response_key}.json"
            if action == "get":
                return store.read(name)
            check()
            return store.put(name, answer)

        classified = classify(turns, provider, redactor, config, checkpoint)
        result = second_pass(fuse(classified, acoustic["diarization"]), provider, config, checkpoint)
        # Outbound text is retained only as a pseudonymized audit artifact.
        result.update(schema_version="2.0", pipeline_version=VERSION, role_key=key,
                      acoustic_key=item["acoustic_key"], conversation_id=item["conversation_id"],
                      role_model=config.role_model, prompt_version=PROMPT_VERSION,
                      redaction_version=REDACTION_VERSION, elapsed_seconds=time.monotonic() - started,
                      source_parts=acoustic["source_parts"], duration_seconds=acoustic["duration_seconds"],
                      speaker_scope="conversation", timestamp_scope="conversation",
                      execution_status="completed", review_status="review_required" if any(
                          t["requires_review"] for t in result["turns"]) or acoustic["quality_status"] ==
                      "review_required" else "unvalidated", attempt_id=attempt)
        result["serialized_level_1"] = "\n".join(
            (f"[{t['start']:.3f}–{t['end']:.3f}] " if t["start"] is not None and t["end"] is not None
             else "[SIN_ALINEACION] ") + t["role_final"] + ": " + t["text"] for t in result["turns"])
        result["serialized_level_2"] = {"transcription": result["turns"],
                                        "speaker_scope": "conversation", "timestamp_scope": "conversation"}
        publish_stage(store, "roles", key, result, attempt, check)
        return result


def publish_bigquery(bq, config, result):
    """Optional sink. A retry republishes saved roles without another model call."""
    target = os.environ.get("PRESENCIAL_BQ_RESULTS_TABLE")
    if not target:
        return
    target = table_id(target)
    from counter_presencial.isolation import Resources
    if target != Resources(config.project).table("counter_presencial_resultados_vnext"):
        raise ValueError("Results must be in the selected project and dedicated dataset")
    from google.cloud import bigquery
    sql = f"""MERGE `{target}` T USING
      (SELECT @key AS role_key, @conversation AS conversation_id, @audio AS acoustic_key,
       @model AS role_model, @prompt AS prompt_version, @review AS review_status,
       PARSE_JSON(@result, wide_number_mode=>'round') AS result) S
      ON T.role_key=S.role_key
      WHEN MATCHED THEN UPDATE SET result=S.result, review_status=S.review_status, updated_at=CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT
        (role_key,conversation_id,acoustic_key,role_model,prompt_version,review_status,result,updated_at)
      VALUES (S.role_key,S.conversation_id,S.acoustic_key,S.role_model,S.prompt_version,S.review_status,S.result,CURRENT_TIMESTAMP())"""
    fields = {"key": result["role_key"], "conversation": result["conversation_id"],
              "audio": result["acoustic_key"], "model": result["role_model"],
              "prompt": result["prompt_version"], "review": result["review_status"],
              "result": json.dumps(result, ensure_ascii=False, allow_nan=False)}
    bq.query(sql, location=config.bq_location, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in fields.items()])).result()


def run_batch(items, stage, function, store):
    failures = []
    for item in items:
        try:
            function(item)
            print(json.dumps({"conversation_id": item["conversation_id"], "stage": stage, "status": "completed"}))
        except Exception as exc:
            failures.append(item["conversation_id"])
            # No transcript, provider exception detail or personal data in logs.
            logging.error("stage=%s conversation=%s error_type=%s", stage, item["conversation_id"], type(exc).__name__)
            store.put(f"failures/{stage}/{uuid.uuid4().hex}.json", {
                "conversation_id": item["conversation_id"], "acoustic_key": item["acoustic_key"],
                "error_type": type(exc).__name__})
    return failures
