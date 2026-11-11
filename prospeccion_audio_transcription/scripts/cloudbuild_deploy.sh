#!/usr/bin/env bash
set -euo pipefail
# Independiente del script CPU. Ningún comando apunta al Job/Workflow CPU.
python_bin="$(command -v python3 || command -v python || true)"
if [[ -z "$python_bin" ]]; then python_bin="$(gcloud info --format='value(basic.python_location)')"; fi
"$python_bin" "${_SOURCE_DIR}/scripts/render_job_env.py"
[[ "${_PROJECT_ID}" == prd-utpbi-data-operation ]] || { echo 'Revisar proyecto y SQL GPU.' >&2; exit 1; }
[[ "${_CPU}" == 4 && "${_MEMORY}" == 16Gi && "${_TASK_TIMEOUT}" == 3600s ]] || {
  echo 'Piloto GPU inicial: 4 CPU, 16Gi y timeout 3600s.' >&2; exit 1;
}
case "${_HF_TOKEN_SOURCE}" in
  manual) secret_args=(--remove-secrets=HF_TOKEN)
    echo 'Configurar HF_TOKEN en el Job GPU después del despliegue; no ejecutar antes.' >&2 ;;
  secret) secret_args=("--update-secrets=HF_TOKEN=${_HF_SECRET}:${_HF_SECRET_VERSION}") ;;
  *) echo 'HF_TOKEN_SOURCE debe ser manual o secret.' >&2; exit 1 ;;
esac
case "${1:-}" in
  validate) exit 0 ;;
  deploy) ;;
  *) exit 1 ;;
esac
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
"$python_bin" "${_SOURCE_DIR}/scripts/render_job_env.py" "$env_file"
manifest_root="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["MANIFEST_GCS_ROOT"])' "$env_file")"
for ddl in 01_create_transcripciones.sql 02_create_intentos.sql; do
  bq --project_id="${_PROJECT_ID}" --location=US query --use_legacy_sql=false < "${_SOURCE_DIR}/sql/$ddl"
done
gcloud run jobs deploy "${_JOB_NAME}" \
  --project="${_PROJECT_ID}" --region="${_LOCATION}" \
  --image="${IMAGE_URI}" --service-account="${_SERVICE_ACCOUNT}" \
  --command=python --args=-m,worker.gpu_main --env-vars-file="$env_file" \
  "${secret_args[@]}" --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu="${_CPU}" --memory="${_MEMORY}" --task-timeout="${_TASK_TIMEOUT}" \
  --tasks=1 --parallelism="${_PARALLELISM}" --max-retries=0 \
  --labels=proyecto=prospeccion,entorno=piloto,acelerador=l4,prueba=gpu-europe-west4 \
  --quiet
gcloud workflows deploy "${_WORKFLOW_NAME}" \
  --project="${_PROJECT_ID}" --location="${_LOCATION}" \
  --source="${_SOURCE_DIR}/workflows/daily_pipeline.yaml" \
  --service-account="${_WORKFLOW_SERVICE_ACCOUNT}" \
  --set-env-vars="^|^PROJECT_ID=${_PROJECT_ID}|LOCATION=${_LOCATION}|JOB_NAME=${_JOB_NAME}|MANIFEST_GCS_ROOT=${manifest_root}" \
  --labels=proyecto=prospeccion,entorno=piloto,acelerador=l4 --quiet
