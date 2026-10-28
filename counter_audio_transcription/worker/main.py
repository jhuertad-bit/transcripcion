import argparse
import json
import logging
import os
from counter_common.contract import AudioRequest
from worker.config import ModelConfig, boolean
from worker.pipeline import run


def process_batch(items, process):
    """Continúa tras fallos individuales; el Job falla si algún archivo falló."""
    failed = 0
    for index, item in enumerate(items, 1):
        try:
            result = process(item)
            print(json.dumps({"batch_index": index, "batch_size": len(items), **result}), flush=True)
        except Exception:
            failed += 1
            logging.exception("Falló archivo %s/%s; se continúa con los siguientes", index, len(items))
    if failed:
        raise RuntimeError(f"Fallaron {failed}/{len(items)} archivos; éxitos ya guardados se conservan")


def main():
    from google.cloud import storage
    from worker.catalog import prepare, read_items
    client = storage.Client(project=os.environ.get("GCP_PROJECT"))
    role = os.environ.get("JOB_ROLE", "single")
    if role == "prepare":
        from google.cloud import bigquery
        prepare(client, bigquery.Client(project=os.environ.get("GCP_PROJECT")))
        return
    if role not in ("single", "worker"):
        raise ValueError("JOB_ROLE debe ser single, prepare o worker")
    items = read_items(client) if role == "worker" else [None]
    parser = argparse.ArgumentParser(description="Counter: procesar una URI GCS por ejecución")
    parser.add_argument("--audio-uri", default=os.environ.get("AUDIO_URI"))
    parser.add_argument("--recording-id", default=os.environ.get("RECORDING_ID"))
    parser.add_argument("--force", action="store_true", default=boolean(os.environ.get("FORCE", "false")))
    args = parser.parse_args()
    if role == "single" and int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")) != 1:
        raise ValueError("Primera versión: una tarea y una URI por ejecución")
    allowed = tuple(b.strip() for b in os.environ["ALLOWED_INPUT_BUCKETS"].split(",") if b.strip())
    if not allowed:
        raise ValueError("ALLOWED_INPUT_BUCKETS requerido")
    config = ModelConfig.from_env()
    from worker.storage import GCSRepository
    sink = None
    if os.environ.get("BQ_RESULTS_TABLE"):
        from google.cloud import bigquery
        from worker.bigquery_sink import BigQuerySink
        sink = BigQuerySink(bigquery.Client(project=os.environ.get("GCP_PROJECT")),
                           os.environ["BQ_RESULTS_TABLE"], os.environ.get("BQ_LOCATION"))
    def process(item):
        body = {"audio_uri": args.audio_uri, "force": args.force}
        if item:
            body.update(audio_uri=item["audio_uri"], recording_id=item["recording_id"])
        if item is None and args.recording_id is not None:
            body["recording_id"] = args.recording_id
        request = AudioRequest.from_dict(body, allowed)
        repo = GCSRepository(client, request.audio_uri, os.environ["OUTPUT_GCS_ROOT"],
                             int(os.environ.get("MAX_AUDIO_BYTES", "524288000")))
        if item and repo.source != item["source"]:
            raise ValueError("El objeto cambió desde prepare; generar otro manifiesto")
        return run(repo, request, config, sink=sink,
                   max_seconds=int(os.environ.get("MAX_AUDIO_SECONDS", "7200")),
                   catalog_metadata=item["catalog"] if item else None)
    process_batch(items, process)



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
