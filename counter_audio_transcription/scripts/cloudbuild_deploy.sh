#!/usr/bin/env bash
set -euo pipefail
for name in _PROJECT_ID _LOCATION _REPOSITORY _SERVICE_ACCOUNT _ALLOWED_INPUT_BUCKETS _OUTPUT_GCS_ROOT _JOB_NAME _HF_SECRET _HF_SECRET_VERSION _CPU _MEMORY _TASK_TIMEOUT; do
  value="${!name:-}"
  if [[ -z "${value//[[:space:]]/}" || "$value" == *YOUR_* ]]; then
    echo "ERROR: completar ${name} en Cloud Build." >&2
    exit 1
  fi
done
if [[ "${_SERVICE_ACCOUNT}" != *@*.iam.gserviceaccount.com || "${_OUTPUT_GCS_ROOT}" != gs://*/* ]]; then
  echo 'ERROR: revisar cuenta de servicio y URI de salida con prefijo.' >&2
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
}
lines = []
for line in Path(sys.argv[1]).read_text(encoding='utf-8-sig').splitlines():
    key = line.split(':', 1)[0]
    lines.append(f'{key}: {json.dumps(values[key])}' if key in values else line)
Path(sys.argv[2]).write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
gcloud run jobs deploy "${_JOB_NAME}" \
  --project="${_PROJECT_ID}" --region="${_LOCATION}" \
  --image="${IMAGE_URI}" --service-account="${_SERVICE_ACCOUNT}" \
  --env-vars-file="$env_file" \
  --set-secrets="HF_TOKEN=${_HF_SECRET}:${_HF_SECRET_VERSION}" \
  --cpu="${_CPU}" --memory="${_MEMORY}" \
  --task-timeout="${_TASK_TIMEOUT}" --tasks=1 --parallelism=1 --max-retries=0 --quiet
