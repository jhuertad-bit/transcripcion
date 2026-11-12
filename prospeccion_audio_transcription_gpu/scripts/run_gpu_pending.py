"""Completa audios pendientes por fecha en GPU, sin Workflows."""
import argparse
from datetime import date, timedelta
import json
import subprocess
import uuid

try:
    from .run_gpu_pilot import PROJECT, REGION, JOB, configuration
except ImportError:
    from run_gpu_pilot import PROJECT, REGION, JOB, configuration

ACCOUNT = 'genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com'


def run(*args, capture=False):
    result = subprocess.run(
        ['gcloud', *args, f'--impersonate-service-account={ACCOUNT}'],
        check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout


def drain_day(day, origin, root, batch_size, max_batches, invoke=run):
    scope = [f'--project={PROJECT}', f'--region={REGION}']
    processed = 0
    for number in range(1, max_batches + 1):
        batch = 'pending-' + uuid.uuid4().hex
        uri = f'{root}/{origin}/{batch}.json'
        print(f'{day} {origin}: lote {number}, manifiesto {uri}', flush=True)
        common = (f'TIPO_ORIGEN={origin},BATCH_MAX_FILES={batch_size},'
                  'FILES_PER_WORKER=1,INCLUDE_COMPLETED=false,FORCE=false')
        invoke('run', 'jobs', 'execute', JOB, *scope, '--tasks=1', '--wait',
               '--update-env-vars=' + common + f',JOB_ROLE=prepare,BATCH_ID={batch},'
               f'PROCESS_DAY_FROM={day},PROCESS_DAY_TO={day}')
        meta = json.loads(invoke('storage', 'cat', uri + '.meta.json',
                                 f'--project={PROJECT}', capture=True))
        count, tasks = meta.get('count'), meta.get('task_count')
        if (type(count) is not int or type(tasks) is not int or
                not 0 <= count <= batch_size or count != tasks):
            raise ValueError('Manifiesto incompatible con un audio por tarea; se detiene.')
        if count == 0:
            print(f'{day} {origin}: sin pendientes. Procesados en esta sesión: {processed}.', flush=True)
            return processed
        invoke('run', 'jobs', 'execute', JOB, *scope, f'--tasks={tasks}', '--wait',
               '--update-env-vars=' + common + f',JOB_ROLE=worker,MANIFEST_URI={uri}')
        processed += count
    raise ValueError(f'{day} {origin}: límite de lotes alcanzado; no se declara fecha completa. '
                     'Volver a ejecutar tras revisar los resultados.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desde', required=True, type=date.fromisoformat)
    parser.add_argument('--hasta', required=True, type=date.fromisoformat)
    parser.add_argument('--origen', required=True, choices=['mentor', 'promotor', 'ambos'])
    parser.add_argument('--lote', type=int, default=5)
    parser.add_argument('--max-lotes-por-fecha', type=int, default=100)
    args = parser.parse_args()
    if args.desde > args.hasta or not 1 <= args.lote <= 100 or args.max_lotes_por_fecha < 1:
        parser.error('Revisar rango, lote (1..100) y máximo de lotes positivo.')
    job = json.loads(run('run', 'jobs', 'describe', JOB, f'--project={PROJECT}',
                         f'--region={REGION}', '--format=json', capture=True))
    root = configuration(job)
    del job
    origins = ['mentor', 'promotor'] if args.origen == 'ambos' else [args.origen]
    day = args.desde
    while day <= args.hasta:
        for origin in origins:
            drain_day(day, origin, root, args.lote, args.max_lotes_por_fecha)
        day += timedelta(days=1)
    print('Rango revisado: no quedan pendientes seleccionables con la configuración GPU actual.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'gcloud falló ({exc.returncode}). Se detuvo el proceso. '
                         'Revisar ejecución y logs antes de reanudar; no lanzar otra copia simultánea.')
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, ValueError) else 'Configuración incompleta; proceso detenido.')
