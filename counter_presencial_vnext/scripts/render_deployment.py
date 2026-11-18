"""Render reviewable local files only. Never contacts GCP or executes commands."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from counter_presencial.isolation import DATASET, PREFIX, Resources

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return shlex.join(str(a) for a in args)


def render(settings):
    r = Resources(settings["project_id"])
    if settings.get("isolated_resources_confirmed") is not True:
        raise ValueError("Confirm that the counter-presencial-vnext resources are dedicated to this pipeline")
    # Fixed initial profile; changing regions requires revisiting GPU availability/data location.
    region = settings.get("region")
    if region != "us-central1":
        raise ValueError("The initial reviewed profile uses us-central1 / BigQuery US")
    for key in ("canary_revision", "nemotron_revision"):
        if not re.fullmatch(r"[a-f0-9]{40}", settings.get(key, "")):
            raise ValueError(key + " must be an immutable 40-character checkpoint commit")
    endpoint = settings.get("gemini_endpoint", "")
    if not re.fullmatch(r"gemini-[a-z0-9.-]{1,80}", endpoint):
        raise ValueError("Specify a supported Gemini endpoint in the selected project")
    first, last = (settings[k] for k in ("process_day_from", "process_day_to"))
    if date.fromisoformat(first) > date.fromisoformat(last):
        raise ValueError("Inverted process dates")
    repository = settings.get("github_repository", "")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or "REPLACE" in repository):
        raise ValueError("Specify the repository hosting the independent pipeline")
    source_dir = settings.get("source_directory", ".")
    if source_dir not in (".", "counter_presencial_vnext"):
        raise ValueError("source_directory must be . or counter_presencial_vnext")
    branch = settings.get("github_branch", "main")
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,128}", branch):
        raise ValueError("Invalid GitHub branch")
    p = r.project
    artifact_repository = r.artifact_repository
    files = {"inventory.json": json.dumps({**r.inventory(), "github_repository": repository,
                                         "source_directory": source_dir, "region": region,
                                         "quota_scope": "shared with other resources in project/region"}, indent=2) + "\n"}
    env = {
        "GCP_PROJECT": p, "GOOGLE_CLOUD_PROJECT": p,
        "ALLOWED_INPUT_BUCKETS": r.bucket("input"),
        "PRESENCIAL_OUTPUT_GCS_ROOT": r.output_root,
        "BQ_SOURCE_TABLE": r.table("catalog_vnext"), "BQ_SOURCE_LOCATION": "US", "BQ_LOCATION": "US",
        "PRESENCIAL_ROLE_MODEL": r.table("gemini_roles_vnext"),
        "PRESENCIAL_BQ_RESULTS_TABLE": r.table("counter_presencial_resultados_vnext"),
        "PROCESS_DAY_FROM": first, "PROCESS_DAY_TO": last,
        "ASR_BACKEND": "canary", "DIARIZATION_BACKEND": "nemotron",
        "MANIFEST_GCS_ROOT": r.output_root + "/manifests", "ROLE_ENABLED": "true",
        "CANARY_REVISION": settings["canary_revision"], "NEMOTRON_REVISION": settings["nemotron_revision"],
        "PRESENCIAL_FILES_PER_TASK": "1", "PRESENCIAL_MAX_CONVERSATIONS": "3",
    }
    # JSON is a YAML subset and can be passed to gcloud --env-vars-file.
    files["runtime.env.yaml"] = json.dumps(env, indent=2) + "\n"
    for name in ("catalog", "results", "model"):
        files[name + ".sql"] = (ROOT / f"sql/{name}.sql.template").read_text().replace(
            "${PROJECT_ID}", p).replace("${GEMINI_ENDPOINT}", endpoint)

    def gc(*args):
        return command("gcloud", *args, "--project=" + p, "--billing-project=" + p)

    def grant(account, role):
        return gc("projects", "add-iam-policy-binding", p,
                  "--member=serviceAccount:" + account, "--role=" + role)

    def bucket_grant(kind, account, role):
        return gc("storage", "buckets", "add-iam-policy-binding", "gs://" + r.bucket(kind),
                  "--member=serviceAccount:" + account, "--role=" + role)

    header = '#!/usr/bin/env bash\nset -euo pipefail\ncd -- "$(dirname -- "$0")/.."\n'
    base = [header, "# Review before executing. Create only our named resources in the selected project.",
            gc("services", "enable", "run.googleapis.com", "cloudbuild.googleapis.com",
               "artifactregistry.googleapis.com", "storage.googleapis.com", "bigquery.googleapis.com",
               "bigqueryconnection.googleapis.com", "aiplatform.googleapis.com", "workflows.googleapis.com",
               "iam.googleapis.com", "iamcredentials.googleapis.com", "logging.googleapis.com")]
    if artifact_repository != PREFIX:
        base.append(gc("artifacts", "repositories", "describe", artifact_repository,
                       "--location=" + region))
    for account in r.inventory()["service_accounts"].values():
        base.append(gc("iam", "service-accounts", "create", account.split("@")[0],
                       "--display-name=" + PREFIX))
    for kind in ("input", "output", "builds"):
        base.append(gc("storage", "buckets", "create", "gs://" + r.bucket(kind),
                       "--location=" + region, "--uniform-bucket-level-access", "--public-access-prevention"))
    if artifact_repository == PREFIX:
        base.append(gc("artifacts", "repositories", "create", artifact_repository,
                       "--location=" + region, "--repository-format=docker"))
    base.append(command("bq", "--project_id=" + p, "--location=US", "mk", "--dataset", p + ":" + DATASET))
    base.append(command("bq", "--project_id=" + p, "--location=US", "mk", "--connection",
                        "--connection_type=CLOUD_RESOURCE", p + ".us." + PREFIX + "-gemini"))
    for stage, role in (("pipeline", "roles/bigquery.jobUser"), ("build", "roles/logging.logWriter")):
        base.append(grant(r.account(stage), role))
    files["dataset_access.sql"] = "\n".join(
        f'GRANT `{role}` ON SCHEMA `{p}.{DATASET}` TO "serviceAccount:{r.account(stage)}";'
        for stage, role in (("pipeline", "roles/bigquery.dataEditor"),)) + "\n"
    base.append(command("bq", "--project_id=" + p, "--location=US", "query", "--use_legacy_sql=false")
                + " < generated/dataset_access.sql")
    base.append(command("python3", "scripts/grant_connection.py", p))
    for stage in ("pipeline",):
        base.append(bucket_grant("input", r.account(stage), "roles/storage.objectViewer"))
    for stage in ("pipeline",):
        base.append(bucket_grant("output", r.account(stage), "roles/storage.objectUser"))
    for stage in ("workflow", "operator"):
        base.append(bucket_grant("output", r.account(stage), "roles/storage.objectViewer"))
    base.append(bucket_grant("builds", r.account("build"), "roles/storage.objectAdmin"))
    if artifact_repository == PREFIX:
        base.append(gc("artifacts", "repositories", "add-iam-policy-binding", artifact_repository,
                       "--location=" + region, "--member=serviceAccount:" + r.account("build"),
                       "--role=roles/artifactregistry.writer"))
    base.append(command("bq", "--project_id=" + p, "--location=US", "show", "--connection",
                        "--format=prettyjson", p + ".us." + PREFIX + "-gemini") + " > generated/connection.json")
    base.append("CPV_CONNECTION_SA=$(python3 -c 'import json; print(json.load(open(\"generated/connection.json\"))[\"cloudResource\"][\"serviceAccountId\"])')")
    # The connection service account comes only from the just-created connection.
    base.append(gc("projects", "add-iam-policy-binding", p, "--role=roles/aiplatform.user")
                + ' --member="serviceAccount:${CPV_CONNECTION_SA}"')
    for name in ("catalog", "results", "model"):
        base.append(command("bq", "--project_id=" + p, "--location=US", "query", "--use_legacy_sql=false")
                    + " < generated/" + name + ".sql")
    files["bootstrap.sh"] = "\n".join(base) + "\n"

    files["build.sh"] = header + gc("builds", "submit", ".", "--config=cloudbuild.yaml",
        "--substitutions=_REPOSITORY=" + artifact_repository,
        "--region=" + region, "--service-account=projects/" + p + "/serviceAccounts/" + r.account("build"),
        "--gcs-source-staging-dir=gs://" + r.bucket("builds") + "/source") + "\n"
    job_lines = [header,
        ': "${CPV_GPU_DIGEST:?Set the sha256 digest produced by the NEW GPU build}"',
        '[[ "$CPV_GPU_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2']
    flags = ["--region=" + region, "--service-account=" + r.account("pipeline"),
             "--env-vars-file=generated/runtime.env.yaml", "--tasks=1", "--parallelism=1",
             "--max-retries=0", "--task-timeout=3600s", "--cpu=8", "--memory=32Gi",
             "--gpu=1", "--gpu-type=nvidia-l4", "--no-gpu-zonal-redundancy"]
    image = f"{region}-docker.pkg.dev/{p}/{artifact_repository}/{PREFIX}/gpu"
    job_lines.append(gc("run", "jobs", "create", PREFIX + "-gpu", *flags)
                     + f' --image="{image}@${{CPV_GPU_DIGEST}}"')
    for principal in ("operator", "workflow"):
        for role in ("roles/run.jobsExecutorWithOverrides", "roles/run.viewer"):
            job_lines.append(gc("run", "jobs", "add-iam-policy-binding", PREFIX + "-gpu",
                "--region=" + region, "--member=serviceAccount:" + r.account(principal), "--role=" + role))
    files["create_jobs.sh"] = "\n".join(job_lines) + "\n"

    owner, repo = repository.split("/")
    files["create_trigger.sh"] = header + "# Use a NEW trigger for this pipeline; never edit Paul's trigger.\n" + gc(
        "builds", "triggers", "create", "github", "--name=" + PREFIX + "-build", "--region=" + region,
        "--repo-owner=" + owner, "--repo-name=" + repo, "--branch-pattern=^" + re.escape(branch) + "$",
        "--build-config=" + ("cloudbuild.yaml" if source_dir == "." else source_dir + "/cloudbuild.yaml"),
        "--included-files=" + ("**" if source_dir == "." else source_dir + "/**"),
        f"--substitutions=_SOURCE_DIR={source_dir},_REPOSITORY={artifact_repository}",
        "--service-account=projects/" + p + "/serviceAccounts/" + r.account("build")) + "\n"
    wf_env = {"PROJECT_ID": p, "LOCATION": region, "JOB_NAME": PREFIX + "-gpu",
              "MANIFEST_GCS_ROOT": r.output_root + "/manifests", "ROLE_ENABLED": "true"}
    files["workflow.env.yaml"] = json.dumps(wf_env, indent=2) + "\n"
    files["optional_workflow.sh"] = header + "# Optional; start with manual Jobs. Refuse to update an existing workflow.\n" + (
        "CPV_EXISTING=$(" + gc("workflows", "list", "--location=" + region, "--format=value(name)",
            "--filter=name:counter-presencial-vnext") + ")\n"
        + 'if [[ -n "$CPV_EXISTING" ]]; then echo "Refusing to replace an existing counter-presencial-vnext workflow" >&2; exit 2; fi\n') + gc(
        "workflows", "deploy", PREFIX, "--location=" + region,
        "--source=workflows/presencial_vnext.yaml", "--env-vars-file=generated/workflow.env.yaml",
        "--service-account=" + r.account("workflow")) + "\n"

    manual = [header,
        "# Same prepare -> worker -> roles contract as Paul's pipeline, on our own Job.",
        "# The caller needs Token Creator only on cpv-operator.",
        'CPV_ROLES_ONLY=0',
        'if [[ "${1:-}" == "--roles-only" ]]; then CPV_ROLES_ONLY=1; shift; [[ "$#" == 1 ]] || exit 2; fi',
        '[[ "$#" -le 1 ]] || exit 2',
        'CPV_BATCH="${1:-$(python3 -c \'import uuid; print(uuid.uuid4().hex)\')}"',
        '[[ "$CPV_BATCH" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || exit 2']
    operator = "--impersonate-service-account=" + r.account("operator")
    def execute():
        return gc("run", "jobs", "execute", PREFIX + "-gpu", "--region=" + region, operator, "--wait")
    manual.append('if [[ "$#" == 0 ]]; then')
    manual.append("  " + execute() + ' --tasks=1 --update-env-vars="JOB_ROLE=prepare,BATCH_ID=${CPV_BATCH}"')
    manual.append("fi")
    manual.append('CPV_META=$(gcloud storage cat "' + r.output_root + '/manifests/${CPV_BATCH}.json.meta.json" '
                  + command("--project=" + p, "--billing-project=" + p, operator) + ")")
    manual.append('CPV_TASKS=$(python3 -c \'import json,sys; print(json.load(sys.stdin)["task_count"])\' <<< "$CPV_META")')
    manual.append('if [[ "$CPV_TASKS" == 0 ]]; then echo "No ready conversations"; exit 0; fi')
    manual.append('[[ "$CPV_TASKS" =~ ^[1-9][0-9]*$ ]] || exit 2')
    manual.append('CPV_MANIFEST="' + r.output_root + '/manifests/${CPV_BATCH}.json"')
    manual.append('if [[ "$CPV_ROLES_ONLY" == 0 ]]; then')
    manual.append("  " + execute() + ' --tasks="$CPV_TASKS" --update-env-vars="JOB_ROLE=worker,MANIFEST_URI=${CPV_MANIFEST}"')
    manual.append("fi")
    manual.append(execute() + ' --tasks=1 --update-env-vars="JOB_ROLE=roles,MANIFEST_URI=${CPV_MANIFEST}"')
    files["run_manual.sh"] = "\n".join(manual) + "\n"

    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "generated")
    args = parser.parse_args()
    files = render(json.loads(args.config.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    for name, contents in files.items():
        (args.output / name).write_text(contents)
    print(f"Rendered {len(files)} local files. No cloud operation performed.")


if __name__ == "__main__":
    main()
