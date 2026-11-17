"""Explicit entry point for isolated prepare / acoustic / roles CPU-GPU jobs."""
import argparse
import logging


def main(stage=None):
    if stage is None:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("stage", choices=("prepare", "acoustic", "roles"))
        stage = parser.parse_args().stage
    if stage not in ("prepare", "acoustic", "roles"):
        raise ValueError("Unknown Presencial stage")
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
        failures = run_batch(items, stage, lambda item: run_acoustic(item, config, store, models), store)
    else:
        bq = bigquery.Client(project=config.project or None)
        provider = BigQueryGemini(bq, config.role_model, config.bq_location)
        redactor = None

        def process(item):
            nonlocal redactor
            acoustic = completed(store, "acoustic", item["acoustic_key"])
            if acoustic is None:
                raise ValueError("Acoustic result pending; retry roles after its successful execution")
            if redactor is None:
                redactor = Redactor()
            # Reuse NLP weights, but placeholders do not link people across recordings.
            result = run_roles(item, acoustic, config, store, provider, redactor.new_conversation())
            publish_bigquery(bq, config, result)
            mark_published(item, result, config, store)

        failures = run_batch(items, stage, process, store)
    if failures:
        raise RuntimeError(f"{len(failures)} conversations pending; successful results are preserved")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
