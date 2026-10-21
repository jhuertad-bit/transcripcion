import argparse
import json
import logging
import os
from counter_common.contract import AudioRequest
from worker.config import ModelConfig, boolean
from worker.pipeline import run


def main():
    parser = argparse.ArgumentParser(description="Counter: procesar una URI GCS por ejecución")
    parser.add_argument("--audio-uri", default=os.environ.get("AUDIO_URI"))
    parser.add_argument("--recording-id", default=os.environ.get("RECORDING_ID"))
    parser.add_argument("--force", action="store_true", default=boolean(os.environ.get("FORCE", "false")))
    args = parser.parse_args()
    if int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")) != 1:
        raise ValueError("Primera versión: una tarea y una URI por ejecución")
    allowed = tuple(b.strip() for b in os.environ["ALLOWED_INPUT_BUCKETS"].split(",") if b.strip())
    if not allowed:
        raise ValueError("ALLOWED_INPUT_BUCKETS requerido")
    body = {"audio_uri": args.audio_uri, "force": args.force}
    if args.recording_id is not None:
        body["recording_id"] = args.recording_id
    request = AudioRequest.from_dict(body, allowed)
    config = ModelConfig.from_env()
    from google.cloud import storage
    from worker.storage import GCSRepository
    client = storage.Client(project=os.environ.get("GCP_PROJECT"))
    repo = GCSRepository(client, request.audio_uri, os.environ["OUTPUT_GCS_ROOT"],
                         int(os.environ.get("MAX_AUDIO_BYTES", "524288000")))
    sink = None
    if os.environ.get("BQ_RESULTS_TABLE"):
        from google.cloud import bigquery
        from worker.bigquery_sink import BigQuerySink
        sink = BigQuerySink(bigquery.Client(project=os.environ.get("GCP_PROJECT")),
                           os.environ["BQ_RESULTS_TABLE"], os.environ.get("BQ_LOCATION"))
    result = run(repo, request, config, sink=sink,
                 max_seconds=int(os.environ.get("MAX_AUDIO_SECONDS", "7200")))
    print(json.dumps(result))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
