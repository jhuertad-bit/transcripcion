"""Clasifica roles de resultados publicados del piloto Counter (CPU y GPU)."""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker.roles import PROMPT_VERSION, gcloud_credentials, run_manifest

PROJECT = 'prd-utpbi-data-operation'
ACCOUNT = 'genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com'
RESULTS = PROJECT + '.raw_queue_smart.counter_piloto_202611_resultados'
VIEW = PROJECT + '.raw_queue_smart.counter_piloto_202611_consulta'
ROLES = PROJECT + '.raw_queue_smart.counter_piloto_202611_roles'
EXPECTED = {'2026-11-02': 49, '2026-11-03': 45, '2026-11-04': 55,
            '2026-11-05': 33, '2026-11-06': 55, '2026-11-07': 28}


def result_root(uri, key):
    marker = '/' + key + '/attempts/'
    if not uri.startswith('gs://') or uri.count(marker) != 1:
        raise ValueError('Ruta de resultado incompatible; no se clasifica.')
    return uri.split(marker, 1)[0]


def main():
    from google.cloud import bigquery, storage
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desde', type=date.fromisoformat, default=date(2026, 11, 2))
    parser.add_argument('--hasta', type=date.fromisoformat, default=date(2026, 11, 7))
    args = parser.parse_args()
    if not date(2026, 11, 2) <= args.desde <= args.hasta <= date(2026, 11, 7):
        parser.error('Rango permitido: 2026-11-02 a 2026-11-07.')
    raw = subprocess.check_output([
        'gcloud', 'run', 'jobs', 'describe', 'prd-utpbi-counter-piloto-202611',
        f'--project={PROJECT}', '--region=us-central1', '--format=json',
        f'--impersonate-service-account={ACCOUNT}'], text=True)
    job = json.loads(raw)
    entries = job['spec']['template']['spec']['template']['spec']['containers'][0].get('env', [])
    env = {e['name']: e['value'] for e in entries if 'value' in e and e['name'] != 'HF_TOKEN'}
    del raw, job, entries
    if env.get('BQ_RESULTS_TABLE') != RESULTS or env.get('BQ_ROLES_TABLE') != ROLES:
        raise ValueError('Revisar tablas del piloto CPU antes de clasificar.')
    model, connection, location = env.get('ROLE_MODEL'), env.get('ROLE_CONNECTION') or None, env.get('BQ_LOCATION')
    if not model or not location:
        raise ValueError('Falta modelo o ubicación de roles en el Job CPU.')
    credentials = gcloud_credentials(ACCOUNT)
    client = bigquery.Client(project=PROJECT, credentials=credentials)
    store = storage.Client(project=PROJECT, credentials=credentials)
    params = [bigquery.ScalarQueryParameter('desde', 'DATE', args.desde),
              bigquery.ScalarQueryParameter('hasta', 'DATE', args.hasta)]
    sql = f'''SELECT process_day, audio_uri, processing_key, result_uri
              FROM `{VIEW}` WHERE process_day BETWEEN @desde AND @hasta
              QUALIFY ROW_NUMBER() OVER(PARTITION BY audio_uri ORDER BY processed_at DESC, processing_key)=1'''
    rows = list(client.query(sql, location=location,
                            job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
    counts = Counter(str(row['process_day']) for row in rows)
    for day, expected in EXPECTED.items():
        if str(args.desde) <= day <= str(args.hasta):
            print(f'{day}: {counts[day]}/{expected} transcripciones', flush=True)
            if counts[day] != expected:
                raise ValueError('Transcripciones incompletas o excedentes; revisar antes de clasificar.')
    existing = set(row['processing_key'] for row in client.query(
        f"SELECT DISTINCT processing_key FROM `{ROLES}` WHERE status='completed' AND model=@model AND prompt_version=@version",
        location=location, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter('model', 'STRING', model),
            bigquery.ScalarQueryParameter('version', 'STRING', PROMPT_VERSION)])).result())
    pending = [row for row in rows if row['processing_key'] not in existing]
    print(f'Total: {len(rows)}; roles ya completados: {len(rows)-len(pending)}; pendientes: {len(pending)}. Modelo: {model}', flush=True)
    for index, row in enumerate(pending, 1):
        print(f'Roles {index}/{len(pending)}: {row["process_day"]} {row["processing_key"]}', flush=True)
        uri, key = row['result_uri'], row['processing_key']
        root = result_root(uri, key)
        bucket, name = uri[5:].split('/', 1)
        result = json.loads(store.bucket(bucket).blob(name).download_as_text())
        if result['processing_key'] != key or result['source']['uri'] != row['audio_uri']:
            raise ValueError('El resultado GCS no coincide con BigQuery.')
        manifest = {'count': 1, 'models': result['models'], 'items': [{
            'recording_id': result['recording_id'], 'source': result['source']}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'roles-manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            run_manifest(store, client, str(path), root, RESULTS, ROLES,
                         location, model, connection)
    print('Roles completados para el rango. Revisar requires_review y la vista de segmentos.')


if __name__ == '__main__':
    main()
