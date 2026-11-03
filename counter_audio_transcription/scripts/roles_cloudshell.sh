#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
case "${1:-}" in
  setup|import|run) action="$1" ;;
  *) echo 'Uso: bash scripts/roles_cloudshell.sh setup|import|run [gs://ruta/manifiesto.json]'; exit 1 ;;
esac
if [[ ! -x .venv-roles/bin/python ]]; then
  python3 -m venv .venv-roles
fi
if ! .venv-roles/bin/python -c "from google.cloud import bigquery, storage" 2>/dev/null; then
  .venv-roles/bin/python -m pip install -r scripts/requirements-roles.txt
fi
common=(
  --project=prd-utpbi-data-operation
  --location=US
  --results-table=prd-utpbi-data-operation.raw_queue_smart.counter_transcripciones_roles_test
  --roles-table=prd-utpbi-data-operation.raw_queue_smart.counter_roles_test
  --model=gemini-3.5-flash
  --impersonate-service-account=genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com
)
if [[ -n "${ROLE_CONNECTION:-}" ]]; then
  common+=("--connection=$ROLE_CONNECTION")
fi
if [[ "$action" == setup ]]; then
  exec .venv-roles/bin/python -m worker.roles "${common[@]}" --setup
fi
common+=(--output-root=gs://prd-utp-stg-queuesmart/counter_audio_transcription_cpu_opt_test/results)
if [[ -n "${2:-}" ]]; then
  common+=("--manifest-uri=$2")
else
  common+=(--manifest-file=samples/manifest_roles_5_test.json)
fi
if [[ "$action" == import ]]; then
  common+=(--import-only)
fi
exec .venv-roles/bin/python -m worker.roles "${common[@]}"
