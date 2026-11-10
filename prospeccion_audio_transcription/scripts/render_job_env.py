"""Construye y valida las variables del Job a partir de Cloud Build."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.config import ModelConfig, boolean
from worker.catalog import table_id
from prospeccion_common.contract import parse_gcs_uri

RUNTIME_KEYS = ('BATCH_MAX_FILES', 'ENABLE_DIARIZATION', 'FILES_PER_WORKER', 'WHISPER_MODEL')


def build_values(template, env):
    values = {}
    for line in template.splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split(':', 1)
            values[key] = json.loads(value.strip())
    for key in RUNTIME_KEYS:
        values[key] = env['_' + key]
    values['GCP_PROJECT'] = env['_PROJECT_ID']
    for key in ('ENABLE_DIARIZATION', 'INCLUDE_COMPLETED', 'FORCE'):
        boolean(values[key])
    cpu = int(env['_CPU'])
    for key in ('WHISPER_CPU_THREADS', 'DIARIZATION_CPU_THREADS'):
        if not 1 <= int(values[key]) <= cpu:
            raise ValueError(f'{key} debe estar entre 1 y CPU')
    for key, maximum in (('FILES_PER_WORKER', 100), ('BATCH_MAX_FILES', 10000)):
        if not 1 <= int(values[key]) <= maximum:
            raise ValueError(f'{key} fuera de rango')
    for key in ('MAX_AUDIO_BYTES', 'MAX_AUDIO_SECONDS'):
        if int(values[key]) < 1:
            raise ValueError(f'{key} debe ser positivo')
    for key in ('BQ_RESULTS_TABLE', 'BQ_ATTEMPTS_TABLE', 'BQ_PROMOTOR_SOURCE_TABLE', 'BQ_MENTOR_SOURCE_TABLE'):
        table_id(values[key])
    if not values['BQ_LOCATION'] or values['BQ_LOCATION'] != values['BQ_SOURCE_LOCATION']:
        raise ValueError('Ubicaciones BigQuery deben coincidir')
    for key in ('OUTPUT_GCS_ROOT', 'MANIFEST_GCS_ROOT'):
        values[key] = values[key].rstrip('/')
        parse_gcs_uri(values[key])
    if not values['ALLOWED_INPUT_BUCKETS'].strip():
        raise ValueError('ALLOWED_INPUT_BUCKETS requerido')
    # Misma validación de modelos que usa el worker, sin modificar el entorno del build.
    ModelConfig(enable_diarization=boolean(values['ENABLE_DIARIZATION']),
                cpu_threads=int(values['WHISPER_CPU_THREADS']),
                diarization_cpu_threads=int(values['DIARIZATION_CPU_THREADS']),
                min_speakers=int(values['MIN_SPEAKERS']), max_speakers=int(values['MAX_SPEAKERS']),
                min_overlap_ratio=float(values['MIN_OVERLAP_RATIO']),
                ambiguity_margin=float(values['AMBIGUITY_MARGIN']))
    if values['MODEL_DEVICE'] != 'cpu':
        raise ValueError('Esta imagen y despliegue son CPU; MODEL_DEVICE debe ser cpu')
    return values


if __name__ == '__main__':
    result = build_values((ROOT/'config/job.env.example.yaml').read_text(encoding='utf-8'), os.environ)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
