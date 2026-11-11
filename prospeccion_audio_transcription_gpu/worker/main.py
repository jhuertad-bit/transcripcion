"""Cloud Run Job: preparar catálogo o procesar una porción del manifiesto."""
import json
import logging
import os
import time
import uuid
from prospeccion_common.contract import AudioRequest
from worker.catalog import prepare, read_items, metadata, output_root, origin
from worker.config import ModelConfig, boolean
from worker.pipeline import run, processing_key, utc_now
from worker.storage import GCSRepository


def process_batch(items, process):
    failed = 0
    for index, item in enumerate(items, 1):
        try:
            print(json.dumps({'batch_index': index, 'batch_size': len(items), **process(item)}), flush=True)
        except Exception:
            failed += 1
            logging.exception('Falló archivo %s/%s; continúan los siguientes', index, len(items))
    if failed:
        raise RuntimeError(f'Fallaron {failed}/{len(items)} archivos; éxitos anteriores se conservan')


def process_item(item, storage_client, config, sink, attempts, execute=run, repository_factory=GCSRepository):
    c = item['catalog']
    tipo = origin(c['tipo_origen'])
    if tipo != origin(os.environ['TIPO_ORIGEN']):
        raise ValueError('El origen del manifiesto no coincide con el Job')
    expected = metadata(c, tipo)['recording_id']
    if item['recording_id'] != expected:
        raise ValueError('Identidad del audio inconsistente')
    allowed = tuple(x.strip() for x in os.environ['ALLOWED_INPUT_BUCKETS'].split(',') if x.strip())
    if not allowed:
        raise ValueError('ALLOWED_INPUT_BUCKETS requerido')
    request = AudioRequest.from_dict({'audio_uri': item['audio_uri'], 'recording_id': expected,
                                     'force': boolean(os.environ.get('FORCE', 'false'))}, allowed)
    if request.audio_uri != c['gcs_uri']:
        raise ValueError('URI inconsistente con el catálogo')
    key = processing_key(request, item['source'], config)
    now, clock = utc_now(), time.monotonic()
    attempt = {
        'attempt_id': uuid.uuid4().hex, 'processing_key': key, 'recording_id': expected,
        'tipo_origen': tipo, 'fecha_audio': c['fecha_audio'], 'gcs_uri': request.audio_uri,
        'audio_generation': item['source']['generation'], 'estado': 'queued', 'etapa': 'descarga',
        'pipeline_version': config.pipeline_version,
        'execution_id': os.environ.get('CLOUD_RUN_EXECUTION'), 'created_at': now,
        'started_at': None, 'updated_at': now, 'finished_at': None, 'elapsed_seconds': None,
        'result_uri': None, 'error_type': None, 'error_message': None,
    }
    # Fallar antes de inferir si el control no puede persistirse.
    attempts.write(dict(attempt))

    def progress(stage):
        attempt.update(estado='processing', etapa=stage, updated_at=utc_now())
        attempts.write(dict(attempt))

    try:
        attempt['started_at'] = utc_now()
        progress('descarga')
        repo = repository_factory(storage_client, request.audio_uri, output_root(tipo),
                                  int(os.environ.get('MAX_AUDIO_BYTES', '524288000')))
        if repo.source != item['source']:
            raise ValueError('El objeto cambió desde prepare; generar otro manifiesto')
        outcome = execute(repo, request, config, sink=sink, catalog_metadata=c,
                          max_seconds=int(os.environ.get('MAX_AUDIO_SECONDS', '7200')),
                          attempt_id=attempt['attempt_id'], progress=progress)
        attempt.update(estado='completed', etapa='publicacion', result_uri=outcome['result_uri'],
                       updated_at=utc_now(), finished_at=utc_now(), elapsed_seconds=round(time.monotonic()-clock, 3))
        attempts.write(dict(attempt))
        return outcome
    except Exception as exc:
        attempt.update(estado='failed', error_type=type(exc).__name__, error_message=str(exc)[:4000],
                       updated_at=utc_now(), finished_at=utc_now(), elapsed_seconds=round(time.monotonic()-clock, 3))
        try:
            attempts.write(dict(attempt))
        except Exception:
            logging.exception('No se pudo persistir el fallo del intento %s', attempt['attempt_id'])
        raise


def main():
    from google.cloud import storage, bigquery
    from worker.bigquery_sink import BigQuerySink, AttemptSink
    client = storage.Client(project=os.environ['GCP_PROJECT'])
    bq = bigquery.Client(project=os.environ['GCP_PROJECT'])
    role = os.environ.get('JOB_ROLE', 'prepare')
    if role == 'prepare':
        prepare(client, bq)
        return
    if role != 'worker':
        raise ValueError('JOB_ROLE debe ser prepare o worker')
    config = ModelConfig.from_env()
    sink = BigQuerySink(bq, os.environ['BQ_RESULTS_TABLE'], os.environ['BQ_LOCATION'])
    attempts = AttemptSink(bq, os.environ['BQ_ATTEMPTS_TABLE'], os.environ['BQ_LOCATION'])
    process_batch(read_items(client), lambda item: process_item(item, client, config, sink, attempts))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
