"""Paul's JOB_ROLE contract on our independent Job; worker uses isolated models."""
import argparse
import logging
import os


def resolve_stage(argument=None, environment=None):
    env = os.environ if environment is None else environment
    aliases = {"prepare": "prepare", "worker": "acoustic", "acoustic": "acoustic", "roles": "roles"}
    role = env.get("JOB_ROLE")
    if role is not None and role not in aliases:
        raise ValueError("Unknown JOB_ROLE")
    if argument is not None and argument not in aliases:
        raise ValueError("Unknown Presencial stage")
    if argument and role and aliases[argument] != aliases[role]:
        raise ValueError("CLI stage conflicts with JOB_ROLE")
    return aliases[argument or role or "worker"]


def main(stage=None):
    if stage is None:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("stage", nargs="?", choices=("prepare", "worker", "acoustic", "roles"))
        stage = parser.parse_args().stage
    stage = resolve_stage(stage)
    from google.cloud import bigquery, storage
    from counter_presencial.config import Config
    from counter_presencial.storage import Store
    from counter_presencial.jobs import (Models, Redactor, BigQueryGemini, completed, manifest_items,
                                        mark_published, prepare, publish_bigquery, run_acoustic, run_batch, run_roles)
    config = Config.from_env()
    storage_client = storage.Client(project=config.project or None)
    store = Store(storage_client, config.output_root)
    if stage == "prepare":
        prepare(config, store, storage_client, bigquery.Client(project=config.project or None))
        return
    items = manifest_items(config, store, storage_client, sharded=stage == "acoustic")
    if stage == "acoustic":
        models = Models(config)
        try:
            failures = run_batch(items, stage, lambda item: run_acoustic(item, config, store, models), store)
        finally:
            models.close()
    else:
        bq = bigquery.Client(project=config.project or None)
        provider = BigQueryGemini(bq, config.role_model, config.bq_location)
        redactor = None

        def process(item):
            nonlocal redactor
            acoustic = completed(store, "acoustic", item["acoustic_key"])
            if acoustic is None:
                raise ValueError("Acoustic result pending; retry roles after its successful execution")
            if redactor is None and acoustic["transcription"]:
                redactor = Redactor()
            # Reuse NLP weights, but placeholders do not link people across recordings.
            scrub = redactor.new_conversation() if acoustic["transcription"] else (lambda text: text)
            result = run_roles(item, acoustic, config, store, provider, scrub)
            publish_bigquery(bq, config, result)
            mark_published(item, result, config, store)

        failures = run_batch(items, stage, process, store)
    if failures:
        raise RuntimeError(f"{len(failures)} conversations pending; successful results are preserved")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
