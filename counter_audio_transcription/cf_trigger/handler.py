"""Solicita una ejecución. No descarga audios ni espera la inferencia."""
import json
import logging
import os
from counter_common.contract import AudioRequest

logger = logging.getLogger(__name__)


def build_run_body(audio_request):
    return {"overrides": {"taskCount": 1, "containerOverrides": [{"env": [
        {"name": name, "value": value}
        for name, value in audio_request.job_environment().items()
    ]}]}}


def submit_job(audio_request):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    project, region, job = (os.environ[k] for k in ("GCP_PROJECT", "GCP_REGION", "COUNTER_JOB_NAME"))
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"
    with AuthorizedSession(credentials) as session:
        # No reintento automático de este POST: un timeout puede ocurrir tras aceptar el job.
        response = session.post(url, json=build_run_body(audio_request), timeout=30)
        response.raise_for_status()
        operation = response.json()
    return operation["name"]


def handle(request, submit=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}

    def reply(body, code):
        return json.dumps(body, ensure_ascii=False), code, headers

    if request.method != "POST":
        headers["Allow"] = "POST"
        return reply({"error": "Usar POST con JSON"}, 405)
    try:
        allowed = tuple(b.strip() for b in os.environ["ALLOWED_INPUT_BUCKETS"].split(",") if b.strip())
        if not allowed:
            raise KeyError("ALLOWED_INPUT_BUCKETS")
        audio_request = AudioRequest.from_dict(request.get_json(silent=True), allowed)
    except ValueError as exc:
        return reply({"error": str(exc)}, 400)
    except KeyError:
        logger.exception("Configuración incompleta de la función")
        return reply({"error": "Configuración de servidor incompleta"}, 500)
    try:
        operation_name = (submit or submit_job)(audio_request)
    except Exception:
        logger.exception("No se pudo confirmar el lanzamiento del job")
        return reply({"error": "Lanzamiento no confirmado. Consultar ejecuciones antes de reintentar."}, 502)
    return reply({"status": "accepted", "recording_id": audio_request.recording_id,
                  "operation_name": operation_name,
                  "message": "Operación aceptada; la transcripción aún no ha finalizado."}, 202)
