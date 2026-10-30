#!/usr/bin/env bash
set -euo pipefail
for name in _PROJECT_ID _LOCATION _REPOSITORY _SERVICE_ACCOUNT _ALLOWED_INPUT_BUCKETS _OUTPUT_GCS_ROOT _JOB_NAME _CPU _MEMORY _TASK_TIMEOUT _BQ_SOURCE_TABLE _BQ_SOURCE_LOCATION _MANIFEST_GCS_ROOT _BATCH_MAX_FILES _PARALLELISM _WORKFLOW_NAME _WORKFLOW_SERVICE_ACCOUNT; do
  value="${!name:-}"
  if [[ -z "${value//[[:space:]]/}" || "$value" == *YOUR_* ]]; then
    echo "ERROR: completar ${name} en Cloud Build." >&2
    exit 1
  fi
done
if [[ ! "${_FILES_PER_WORKER}" =~ ^[1-9][0-9]*$ || ! "${_WHISPER_CPU_THREADS}" =~ ^[1-9][0-9]*$ ]]; then
  echo 'ERROR: FILES_PER_WORKER y WHISPER_CPU_THREADS deben ser positivos.' >&2
  exit 1
fi
if (( _FILES_PER_WORKER > 100 || _WHISPER_CPU_THREADS > _CPU )); then
  echo 'ERROR: maximo 100 archivos por worker; hilos Whisper no deben superar CPU.' >&2
  exit 1
fi
case "${_ENABLE_DIARIZATION:-}" in
  false) secret_args=(--remove-secrets=HF_TOKEN) ;;
  true)
    case "${_HF_TOKEN_SOURCE:-secret}" in
      secret)
        if [[ -z "${_HF_SECRET:-}" || -z "${_HF_SECRET_VERSION:-}" ]]; then
          echo 'ERROR: completar secreto HF y versión para diarización.' >&2
          exit 1
        fi
        secret_args=("--update-secrets=HF_TOKEN=${_HF_SECRET}:${_HF_SECRET_VERSION}") ;;
      manual)
        secret_args=(--remove-secrets=HF_TOKEN)
        echo 'AVISO: agregar HF_TOKEN en Cloud Run despues de CADA despliegue y ANTES de ejecutar el Workflow.' >&2 ;;
      *) echo 'ERROR: _HF_TOKEN_SOURCE debe ser secret o manual.' >&2; exit 1 ;;
    esac ;;
  *) echo 'ERROR: _ENABLE_DIARIZATION debe ser true o false.' >&2; exit 1 ;;
esac
if [[ "${_SERVICE_ACCOUNT}" != *@*.iam.gserviceaccount.com || "${_OUTPUT_GCS_ROOT}" != gs://*/* ]]; then
  echo 'ERROR: revisar cuenta de servicio y URI de salida con prefijo.' >&2
  exit 1
fi
if [[ "${_WORKFLOW_SERVICE_ACCOUNT}" != *@*.iam.gserviceaccount.com || "${_MANIFEST_GCS_ROOT}" != gs://*/* ]]; then
  echo 'ERROR: revisar cuenta del Workflow y prefijo de manifiestos.' >&2
  exit 1
fi
if [[ ! "${_BATCH_MAX_FILES}" =~ ^[0-9]+$ || ! "${_PARALLELISM}" =~ ^[0-9]+$ ]]; then
  echo 'ERROR: tamaño de lote y concurrencia deben ser enteros positivos.' >&2
  exit 1
fi
if (( _BATCH_MAX_FILES < 1 || _BATCH_MAX_FILES > 10000 || _PARALLELISM < 1 || _PARALLELISM > 10000 )); then
  echo 'ERROR: tamaño de lote/concurrencia fuera de rango 1-10000.' >&2
  exit 1
fi
if [[ -n "${_BQ_RESULTS_TABLE}" && -z "${_BQ_LOCATION}" ]]; then
  echo 'ERROR: completar _BQ_LOCATION para habilitar resultados BigQuery.' >&2
  exit 1
fi
case "${1:-}" in
  validate) exit 0 ;;
  deploy) ;;
  *) echo 'Uso: cloudbuild_deploy.sh validate|deploy' >&2; exit 1 ;;
esac
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
python3 - "${_SOURCE_DIR}/config/job.env.example.yaml" "$env_file" <<'PY'
import json
import os
import sys
from pathlib import Path
values = {
    'GCP_PROJECT': os.environ['_PROJECT_ID'],
    'ALLOWED_INPUT_BUCKETS': os.environ['_ALLOWED_INPUT_BUCKETS'],
    'OUTPUT_GCS_ROOT': os.environ['_OUTPUT_GCS_ROOT'],
    'ENABLE_DIARIZATION': os.environ['_ENABLE_DIARIZATION'],
    'FILES_PER_WORKER': os.environ['_FILES_PER_WORKER'],
    'WHISPER_CPU_THREADS': os.environ['_WHISPER_CPU_THREADS'],
    'WHISPER_MODEL': os.environ['_WHISPER_MODEL'],
}
for key in ('BQ_SOURCE_TABLE', 'BQ_SOURCE_LOCATION', 'BQ_RESULTS_TABLE', 'BQ_LOCATION', 'MANIFEST_GCS_ROOT', 'BATCH_MAX_FILES', 'PROCESS_DAY_FROM', 'PROCESS_DAY_TO'):
    values[key] = os.environ['_' + key]
values['MANIFEST_GCS_ROOT'] = values['MANIFEST_GCS_ROOT'].rstrip('/')
if values['BQ_RESULTS_TABLE'] and not values['BQ_LOCATION']:
    raise ValueError('BQ_LOCATION requerido para resultados')
if not 1 <= int(values['BATCH_MAX_FILES']) <= 10000:
    raise ValueError('BATCH_MAX_FILES fuera de rango')
lines = []
for line in Path(sys.argv[1]).read_text(encoding='utf-8-sig').splitlines():
    key = line.split(':', 1)[0]
    lines.append(f'{key}: {json.dumps(values[key])}' if key in values else line)
for key, value in values.items():
    if not any(line.startswith(key + ':') for line in lines):
        lines.append(f'{key}: {json.dumps(value)}')
Path(sys.argv[2]).write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
gcloud run jobs deploy "${_JOB_NAME}" \
  --project="${_PROJECT_ID}" --region="${_LOCATION}" \
  --image="${IMAGE_URI}" --service-account="${_SERVICE_ACCOUNT}" \
  --env-vars-file="$env_file" \
  "${secret_args[@]}" \
  --cpu="${_CPU}" --memory="${_MEMORY}" \
  --task-timeout="${_TASK_TIMEOUT}" --tasks=1 --parallelism="${_PARALLELISM}" --max-retries=0 --quiet

gcloud workflows deploy "${_WORKFLOW_NAME}" \
  --project="${_PROJECT_ID}" --location="${_LOCATION}" \
  --source="${_SOURCE_DIR}/workflows/daily_pipeline.yaml" \
  --service-account="${_WORKFLOW_SERVICE_ACCOUNT}" \
  --set-env-vars="PROJECT_ID=${_PROJECT_ID},LOCATION=${_LOCATION},JOB_NAME=${_JOB_NAME},MANIFEST_GCS_ROOT=${_MANIFEST_GCS_ROOT%/}" --quiet
