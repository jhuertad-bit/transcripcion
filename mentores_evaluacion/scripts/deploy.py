"""Despliega recursos exclusivos de mentores desde Cloud Build."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def call(*args, capture=False):
    return subprocess.run(["gcloud", *args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def main():
    e = os.environ
    enable_scheduler = e.get("ENABLE_SCHEDULER", "false").lower()
    if enable_scheduler not in ("true", "false"):
        raise ValueError("ENABLE_SCHEDULER debe ser true o false")
    if e["PROJECT"] != "prd-utpbi-data-operation":
        raise ValueError("El SQL de este paquete pertenece a prd-utpbi-data-operation")
    for key in ("LOCATION", "REPOSITORY", "SERVICE_ACCOUNT", "WORKFLOW_SERVICE_ACCOUNT", "SCHEDULE"):
        if not e.get(key):
            raise ValueError("Falta " + key)
    for key in ("JOB_NAME", "WORKFLOW_NAME", "SCHEDULER_NAME"):
        if "mentores-evaluacion" not in e[key]:
            raise ValueError("Los nombres deben identificar mentores-evaluacion: " + key)
    project, region = e["PROJECT"], e["LOCATION"]
    # No ejecuta inferencia durante el despliegue. Init solo crea tablas faltantes y prompts.
    call("run", "jobs", "deploy", e["JOB_NAME"], "--project=" + project, "--region=" + region,
         "--image=" + e["IMAGE_URI"], "--service-account=" + e["SERVICE_ACCOUNT"],
         "--command=python", "--args=-m,worker.main,run",
         "--set-env-vars=BQ_LOCATION=" + e["BQ_LOCATION"] + ",FIRESTORE_PROJECT=citapromotor-utp,FIRESTORE_CREDENTIALS=/secrets/firestore/key.json",
         "--set-secrets=/secrets/firestore/key.json=" + e["FIRESTORE_SECRET"] + ":latest",
         "--cpu=1", "--memory=1Gi", "--task-timeout=25200s", "--tasks=1",
         "--parallelism=1", "--max-retries=0", "--quiet")
    call("run", "jobs", "execute", e["JOB_NAME"], "--project=" + project, "--region=" + region,
         "--args=-m,worker.main,init", "--wait", "--quiet")
    call("workflows", "deploy", e["WORKFLOW_NAME"], "--project=" + project, "--location=" + region,
         "--source=" + str(Path(e["SOURCE_DIR"]) / "workflows" / "daily_pipeline.yaml"),
         "--service-account=" + e["WORKFLOW_SERVICE_ACCOUNT"],
         f"--set-env-vars=PROJECT_ID={project},LOCATION={region},JOB_NAME={e['JOB_NAME']}", "--quiet")
    uri = (f"https://workflowexecutions.googleapis.com/v1/projects/{project}/locations/{region}"
           f"/workflows/{e['WORKFLOW_NAME']}/executions")
    existing = json.loads(call("scheduler", "jobs", "list", "--project=" + project,
                              "--location=" + region, "--format=json(name)", capture=True))
    exists = any(j["name"].rsplit("/", 1)[-1] == e["SCHEDULER_NAME"] for j in existing)
    if enable_scheduler == "false":
        if exists:
            call("scheduler", "jobs", "pause", e["SCHEDULER_NAME"],
                 "--project=" + project, "--location=" + region, "--quiet")
        print("Despliegue completo: Job y Workflow listos para ejecucion manual; Scheduler deshabilitado")
        return
    with tempfile.TemporaryDirectory() as temp:
        body = Path(temp) / "scheduler.json"
        body.write_text(json.dumps({"argument": "{}"}), encoding="utf-8")
        call("scheduler", "jobs", "update" if exists else "create", "http", e["SCHEDULER_NAME"],
             "--project=" + project, "--location=" + region, "--schedule=" + e["SCHEDULE"],
             "--time-zone=" + e["TIME_ZONE"], "--uri=" + uri, "--http-method=POST",
             "--oauth-service-account-email=" + e["WORKFLOW_SERVICE_ACCOUNT"],
             "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform",
             "--headers=Content-Type=application/json", "--message-body-from-file=" + str(body),
             "--max-retry-attempts=0", "--quiet")
    if exists:
        call("scheduler", "jobs", "resume", e["SCHEDULER_NAME"],
             "--project=" + project, "--location=" + region, "--quiet")
    print("Despliegue completo: Cloud Run Job, Workflow y Scheduler de mentores")


if __name__ == "__main__":
    main()
