"""Despliega Job Counter GPU para el 07/11 con las mismas tablas del piloto CPU."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

PROJECT = 'prd-utpbi-data-operation'
SOURCE = 'prd-utpbi-counter-piloto-202611'
TARGET = 'prd-utpbi-counter-piloto-202611-gpu'
ACCOUNT = 'genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com'


def gcloud(*args, capture=False):
    result = subprocess.run(['gcloud', *args, f'--project={PROJECT}',
                             f'--impersonate-service-account={ACCOUNT}'], check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout


def gpu_values(source):
    if source['metadata']['name'] != SOURCE:
        raise ValueError('Usar como origen el Job piloto CPU de Counter.')
    task = source['spec']['template']['spec']['template']['spec']
    if len(task['containers']) != 1:
        raise ValueError('Se requiere un solo contenedor.')
    env = {e['name']: e for e in task['containers'][0].get('env', [])}
    # No copiar HF_TOKEN, referencias a secretos regionales ni otras credenciales.
    if any('valueFrom' in e for k, e in env.items() if k != 'HF_TOKEN'):
        raise ValueError('Hay otros secretos; revisar explícitamente antes de copiar configuración.')
    values = {k: e['value'] for k, e in env.items() if k != 'HF_TOKEN' and 'value' in e}
    if values.get('MODEL_DEVICE', 'cpu') != 'cpu':
        raise ValueError('El origen debe usar CPU.')
    if not values.get('ALLOWED_INPUT_BUCKETS') or not values.get('BQ_SOURCE_TABLE'):
        raise ValueError('Faltan entradas del catálogo.')
    tables = []
    for key in ('BQ_RESULTS_TABLE', 'BQ_ROLES_TABLE'):
        table = values.get(key)
        if table:
            if not re.fullmatch(r'prd-utpbi-data-operation\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', table) or table.endswith('_gpu'):
                raise ValueError('Revisar tabla CPU antes de generar el destino GPU.')
            # El piloto del 07/11 continúa en sus tablas actuales.
    expected_results = f'{PROJECT}.raw_queue_smart.counter_piloto_202611_resultados'
    expected_source = f'{PROJECT}.raw_queue_smart.counter_piloto_202611_entrada'
    if values.get('BQ_RESULTS_TABLE') != expected_results or values.get('BQ_SOURCE_TABLE') != expected_source:
        raise ValueError('El origen debe apuntar a las tablas de entrada y resultados del piloto Counter.')
    if not values.get('BQ_RESULTS_TABLE') or not values.get('BQ_LOCATION'):
        raise ValueError('El piloto debe tener tabla de resultados y ubicación BigQuery.')
    for key in ('OUTPUT_GCS_ROOT', 'MANIFEST_GCS_ROOT'):
        root = values[key].rstrip('/')
        if not root.startswith('gs://') or '/' not in root[5:]:
            raise ValueError('Prefijo GCS inválido.')
        values[key] = root + '/gpu'
    for key in ('AUDIO_URI', 'RECORDING_ID', 'MANIFEST_URI', 'BATCH_ID'):
        values.pop(key, None)
    values.update(MODEL_DEVICE='cuda', WHISPER_COMPUTE_TYPE='float16',
                  PIPELINE_VERSION='counter-gpu-v1.0.0', FILES_PER_WORKER='1',
                  BATCH_MAX_FILES='1', JOB_ROLE='prepare', INCLUDE_COMPLETED='false', FORCE='false',
                  PROCESS_DAY_FROM='2026-11-07', PROCESS_DAY_TO='2026-11-07',
                  NOISE_REDUCTION='false',
                  WHISPER_CPU_THREADS='4', DIARIZATION_CPU_THREADS='4')
    return values, tables, task.get('serviceAccountName')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f-]{36}', args.build_id):
        parser.error('Indicar el ID del build GPU exitoso.')
    source = json.loads(gcloud('run', 'jobs', 'describe', SOURCE,
                                '--region=us-central1', '--format=json', capture=True))
    values, tables, account = gpu_values(source)
    del source
    if not account or not account.endswith('.iam.gserviceaccount.com'):
        raise ValueError('Revisar cuenta de ejecución del Job CPU.')
    print('Resultados en la tabla existente:', values['BQ_RESULTS_TABLE'], flush=True)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'gpu-env.json'
        path.write_text(json.dumps(values), encoding='utf-8')
        # Timeout GPU de 7200s requiere la etapa Beta (long-running GPU jobs).
        gcloud('beta', 'run', 'jobs', 'deploy', TARGET, '--region=europe-west4',
               f'--image=us-central1-docker.pkg.dev/{PROJECT}/serializa-audio-repo/counter-audio-transcription-gpu:{args.build_id}',
               f'--service-account={account}', '--command=python', '--args=-m,worker.gpu_main',
               f'--env-vars-file={path}', '--remove-secrets=HF_TOKEN',
               '--gpu=1', '--gpu-type=nvidia-l4', '--no-gpu-zonal-redundancy',
               '--cpu=4', '--memory=16Gi', '--task-timeout=7200s', '--tasks=1',
               '--parallelism=1', '--max-retries=0',
               '--labels=proyecto=counter,entorno=piloto,acelerador=l4', '--quiet')
    print('Counter GPU desplegado sin ejecutar audios ni Workflows.')
    print('Configurado para 2026-11-07, sin filtro de ruido. No ejecutar prepare/worker todavía:')
    print('primero verificar que CPU no esté activo y seleccionar pendientes por audio_uri en BigQuery.')
    print('Agregar HF_TOKEN al Job GPU antes de ejecutar diarización. No se copió el token CPU.')


if __name__ == '__main__':
    main()
