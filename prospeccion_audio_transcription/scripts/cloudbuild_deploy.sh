#!/usr/bin/env bash
set -euo pipefail
for key in _PROJECT_ID _LOCATION _REPOSITORY _SERVICE_ACCOUNT _WORKFLOW_SERVICE_ACCOUNT _JOB_NAME _WORKFLOW_NAME; do
  value="${!key:-}"
  if [[ -z "$value" || "$value" == *YOUR_* ]]; then
    echo "Completar $key en Cloud Build" >&2; exit 1
  fi
done
if [[ "${_ENABLE_DIARIZATION}" == false ]]; then
  secret_args=(--remove-secrets=HF_TOKEN)
else
case "${_HF_TOKEN_SOURCE:-secret}" in
  manual)
    secret_args=(--remove-secrets=HF_TOKEN)
    echo 'Agregar HF_TOKEN en las variables del Job después de cada despliegue y antes de ejecutar.' >&2 ;;
  secret)
    [[ -n "${_HF_SECRET:-}" && -n "${_HF_SECRET_VERSION:-}" ]] || { echo 'Completar secreto HF y versión.' >&2; exit 1; }
    secret_args=("--update-secrets=HF_TOKEN=${_HF_SECRET}:${_HF_SECRET_VERSION}") ;;
  *) echo '_HF_TOKEN_SOURCE debe ser manual o secret.' >&2; exit 1 ;;
esac
fi
for key in _CPU _PARALLELISM _BATCH_MAX_FILES _FILES_PER_WORKER; do
  value="${!key:-}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo "$key debe ser positivo" >&2; exit 1; }
done
(( _FILES_PER_WORKER <= 100 && _BATCH_MAX_FILES <= 10000 && _PARALLELISM <= 10000 && _CPU >= 4 )) || {
  echo 'Límites: FILES_PER_WORKER<=100, BATCH_MAX_FILES/PARALLELISM<=10000, CPU>=4' >&2; exit 1;
}
python_bin="$(command -v python3 || command -v python || true)"
if [[ -z "$python_bin" ]]; then python_bin="$(gcloud info --format='value(basic.python_location)')"; fi
"$python_bin" "${_SOURCE_DIR}/scripts/render_job_env.py"
case "${1:-}" in
  validate) exit 0 ;;
  deploy) ;;
  *) echo 'Uso: cloudbuild_deploy.sh validate|deploy' >&2; exit 1 ;;
esac
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
"$python_bin" "${_SOURCE_DIR}/scripts/render_job_env.py" "$env_file"
manifest_root="$("$python_bin" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["MANIFEST_GCS_ROOT"])' "$env_file")"
gcloud run jobs deploy "${_JOB_NAME}" \
  --project="${_PROJECT_ID}" --region="${_LOCATION}" \
  --image="${IMAGE_URI}" --service-account="${_SERVICE_ACCOUNT}" \
  --command=python --args=-m,worker.main --env-vars-file="$env_file" \
  "${secret_args[@]}" \
  --cpu="${_CPU}" --memory="${_MEMORY}" --task-timeout="${_TASK_TIMEOUT}" \
  --tasks=1 --parallelism="${_PARALLELISM}" --max-retries=0 --quiet
gcloud workflows deploy "${_WORKFLOW_NAME}" \
  --project="${_PROJECT_ID}" --location="${_LOCATION}" \
  --source="${_SOURCE_DIR}/workflows/daily_pipeline.yaml" \
  --service-account="${_WORKFLOW_SERVICE_ACCOUNT}" \
  --set-env-vars="^|^PROJECT_ID=${_PROJECT_ID}|LOCATION=${_LOCATION}|JOB_NAME=${_JOB_NAME}|MANIFEST_GCS_ROOT=${manifest_root}" \
  --quiet
