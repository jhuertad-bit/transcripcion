"""Canonical resource namespace inside the selected GCP project, including PRD."""
from dataclasses import dataclass
import os
import re

PREFIX = "counter-presencial-vnext"
DATASET = "counter_presencial_vnext"


@dataclass(frozen=True)
class Resources:
    project: str

    def __post_init__(self):
        if (not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", self.project)
                or self.project.startswith(("YOUR", "replace", "REPLACE"))):
            raise ValueError("A valid GCP project ID is required")

    def bucket(self, kind):
        if kind not in ("input", "output", "builds"):
            raise ValueError("Unknown bucket kind")
        return f"{self.project}-cpv-{kind}"

    def table(self, name):
        if name not in ("catalog_vnext", "counter_presencial_resultados_vnext",
                        "counter_presencial_segmentos_vnext", "gemini_roles_vnext"):
            raise ValueError("Unknown isolated BigQuery resource")
        return f"{self.project}.{DATASET}.{name}"

    def account(self, stage):
        if stage not in ("prepare", "gpu", "roles", "build", "workflow", "operator"):
            raise ValueError("Unknown service account")
        return f"cpv-{stage}@{self.project}.iam.gserviceaccount.com"

    @property
    def output_root(self):
        return f"gs://{self.bucket('output')}/presencial-vnext"

    def inventory(self):
        return {
            "project": self.project,
            "jobs": {stage: PREFIX + "-" + stage for stage in ("prepare", "gpu", "roles")},
            "workflow": PREFIX,
            "trigger": PREFIX + "-build",
            "artifact_repository": PREFIX,
            "images": [PREFIX + "-cpu", PREFIX + "-gpu"],
            "buckets": {k: self.bucket(k) for k in ("input", "output", "builds")},
            "dataset": f"{self.project}.{DATASET}",
            "catalog": self.table("catalog_vnext"),
            "results": self.table("counter_presencial_resultados_vnext"),
            "view": self.table("counter_presencial_segmentos_vnext"),
            "remote_model": self.table("gemini_roles_vnext"),
            "connection": PREFIX + "-gemini",
            "service_accounts": {k: self.account(k) for k in
                                 ("prepare", "gpu", "roles", "build", "workflow", "operator")},
            "scheduler": "disabled; manual Cloud Shell execution only",
            "eventarc_pubsub": "unused; no shared audio events",
            "secrets": "none required; optional future secrets must belong to this project",
        }

    def validate_env(self):
        expected = {
            "GCP_PROJECT": self.project,
            "GOOGLE_CLOUD_PROJECT": self.project,
            "ALLOWED_INPUT_BUCKETS": self.bucket("input"),
            "PRESENCIAL_OUTPUT_GCS_ROOT": self.output_root,
            "BQ_SOURCE_TABLE": self.table("catalog_vnext"),
            "PRESENCIAL_ROLE_MODEL": self.table("gemini_roles_vnext"),
            "PRESENCIAL_BQ_RESULTS_TABLE": self.table("counter_presencial_resultados_vnext"),
        }
        for key, value in expected.items():
            if os.environ.get(key) != value:
                raise ValueError(f"{key} does not match the independent resource contract")
        mapping = os.environ.get("PRESENCIAL_ORIGINALS_MAP_URI")
        if mapping and not mapping.startswith(f"gs://{self.bucket('input')}/"):
            raise ValueError("Originals mapping must be in the dedicated input bucket")
        for key in ("BQ_LOCATION", "BQ_SOURCE_LOCATION"):
            if os.environ.get(key, "").upper() != "US":
                raise ValueError("This reviewed deployment profile uses BigQuery US")
