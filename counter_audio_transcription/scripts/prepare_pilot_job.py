"""Genera un Job independiente desde gcloud run jobs describe --format=json.

No despliega ni modifica el Job de origen. Conserva imagen, modelos y recursos.
"""
import argparse
import json
from pathlib import Path


def build(source, location):
    name = 'prd-utpbi-counter-piloto-202611'
    if source['metadata']['name'] == name:
        raise ValueError('El origen debe ser el Job habitual, no el piloto')
    spec = source['spec']
    task = spec['template']['spec']['template']['spec']
    if len(task['containers']) != 1:
        raise ValueError('Se requiere un Job con un contenedor')
    container = task['containers'][0]
    env = {item['name']: item for item in container.get('env', [])}
    root = env['OUTPUT_GCS_ROOT']['value'].rstrip('/') + '/piloto-202611'
    overrides = {
        'BQ_SOURCE_TABLE': 'prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_entrada',
        'BQ_RESULTS_TABLE': 'prd-utpbi-data-operation.raw_queue_smart.counter_piloto_202611_resultados',
        'BQ_SOURCE_LOCATION': location, 'BQ_LOCATION': location,
        'OUTPUT_GCS_ROOT': root + '/results', 'MANIFEST_GCS_ROOT': root + '/manifests',
        'BQ_ROLES_TABLE': '', 'JOB_ROLE': 'prepare', 'BATCH_MAX_FILES': '100',
        'PROCESS_DAY_FROM': '2026-11-02', 'PROCESS_DAY_TO': '2026-11-02',
        'INCLUDE_COMPLETED': 'false', 'FORCE': 'false',
    }
    for key, value in overrides.items():
        env[key] = {'name': key, 'value': value}
    for key in ('AUDIO_URI', 'RECORDING_ID', 'MANIFEST_URI', 'BATCH_ID'):
        env.pop(key, None)
    container['env'] = list(env.values())
    container['command'] = ['python']
    container['args'] = ['-m', 'worker.main']
    spec['template']['spec']['taskCount'] = 1
    task['maxRetries'] = 0
    # Conservar la plantilla de ejecución, sin metadatos del recurso original.
    return {'apiVersion': source['apiVersion'], 'kind': source['kind'],
            'metadata': {'name': name}, 'spec': spec}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--bq-location', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8-sig'))
    Path(args.destination).write_text(json.dumps(build(source, args.bq_location), indent=2), encoding='utf-8')
