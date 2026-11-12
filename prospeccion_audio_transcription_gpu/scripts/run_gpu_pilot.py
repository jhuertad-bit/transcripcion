"""Piloto de un audio en Cloud Run, sin Workflows ni dependencias Python externas."""
import argparse
from datetime import date
import json
import subprocess
import uuid

PROJECT = 'prd-utpbi-data-operation'
REGION = 'europe-west4'
JOB = 'prd-utpbi-prospeccion-audio-transcripcion-gpu'


def command(*args, capture=False):
    result = subprocess.run(['gcloud', *args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def configuration(job):
    template = job['spec']['template']['spec']['template']['spec']
    container = template['containers'][0]
    env = {entry['name']: entry for entry in container.get('env', [])}
    def value(name):
        return env.get(name, {}).get('value', '')
    if value('MODEL_DEVICE') != 'cuda' or value('WHISPER_COMPUTE_TYPE') != 'float16':
        raise ValueError('El Job debe usar cuda y float16.')
    if str(container.get('resources', {}).get('limits', {}).get('nvidia.com/gpu')) != '1':
        raise ValueError('El piloto requiere una GPU por tarea.')
    if template.get('nodeSelector', {}).get('run.googleapis.com/accelerator') != 'nvidia-l4':
        raise ValueError('El piloto requiere NVIDIA L4.')
    if int(job['spec']['template']['spec'].get('parallelism', 1)) != 1:
        raise ValueError('El piloto requiere paralelismo 1.')
    if value('ENABLE_DIARIZATION').lower() == 'true':
        token = env.get('HF_TOKEN', {})
        if not (token.get('value') or token.get('valueFrom', {}).get('secretKeyRef')):
            raise ValueError('Agregar HF_TOKEN al Job antes de ejecutar. No pasarlo al script.')
    root = value('MANIFEST_GCS_ROOT').rstrip('/')
    if not root.startswith('gs://') or '/prospeccion_gpu/' not in root + '/':
        raise ValueError('MANIFEST_GCS_ROOT debe apuntar a la ruta exclusiva GPU.')
    return root


def validate_manifest(meta):
    count, tasks = meta.get('count'), meta.get('task_count')
    if type(count) is not int or type(tasks) is not int or (count, tasks) not in ((0, 0), (1, 1)):
        raise ValueError('El manifiesto excede el piloto o tiene conteos inconsistentes.')
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fecha', required=True, type=date.fromisoformat)
    parser.add_argument('--origen', required=True, choices=['promotor', 'mentor'])
    args = parser.parse_args()
    scope = [f'--project={PROJECT}', f'--region={REGION}']
    # La respuesta contiene variables del Job: nunca imprimirla ni guardarla en disco.
    job = json.loads(command('run', 'jobs', 'describe', JOB, *scope,
                             '--format=json', capture=True))
    root = configuration(job)
    del job
    batch = 'pilot-' + uuid.uuid4().hex
    uri = f'{root}/{args.origen}/{batch}.json'
    print(f'Lote: {batch}\nManifiesto: {uri}', flush=True)
    common = (f'TIPO_ORIGEN={args.origen},BATCH_MAX_FILES=1,FILES_PER_WORKER=1,'
              'INCLUDE_COMPLETED=false,FORCE=false')
    command('run', 'jobs', 'execute', JOB, *scope, '--tasks=1', '--wait',
            '--update-env-vars=' + common + f',JOB_ROLE=prepare,BATCH_ID={batch},'
            f'PROCESS_DAY_FROM={args.fecha},PROCESS_DAY_TO={args.fecha}')
    meta = json.loads(command('storage', 'cat', uri + '.meta.json',
                              f'--project={PROJECT}', capture=True))
    if not validate_manifest(meta):
        print('Sin audios pendientes para esa fecha y origen. No se lanza worker.')
        return
    command('run', 'jobs', 'execute', JOB, *scope, '--tasks=1', '--wait',
            '--update-env-vars=' + common + f',JOB_ROLE=worker,MANIFEST_URI={uri}')
    print('Piloto finalizado. Revisar texto, diarización y tiempos en GCS y tablas GPU.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
        # No mostrar respuestas del Job que puedan contener HF_TOKEN.
        if isinstance(exc, subprocess.CalledProcessError):
            raise SystemExit(f'gcloud falló (código {exc.returncode}). Revisar el error anterior; no se continúa.')
        if isinstance(exc, KeyError):
            raise SystemExit('Respuesta de Cloud Run incompleta; no se ejecuta el piloto.')
        raise SystemExit(str(exc))
