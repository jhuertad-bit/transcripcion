"""Configuration isolated from the existing Counter jobs and result keys."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from counter_presencial.shared import parse_gcs_uri

VERSION = "counter-presencial-vnext-2-saxo"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    output_root: str
    allowed_buckets: tuple
    project: str = ""
    bq_location: str = "US"
    role_model: str = ""
    canary_model: str = "nvidia/canary-1b-v2"
    canary_revision: str = ""
    nemotron_model: str = "nvidia/Nemotron-3-Diarization"
    nemotron_revision: str = ""
    asr_backend: str = "canary"
    diarization_backend: str = "nemotron"
    max_audio_seconds: int = 7200
    max_audio_bytes: int = 524288000
    files_per_task: int = 3
    max_conversations: int = 100
    window_seconds: int = 24
    max_prompt_chars: int = 180000
    targets_per_call: int = 40
    max_second_pass: int = 8
    max_first_pass_calls: int = 64
    speaker_min_seconds: float = 1.0
    speaker_min_ratio: float = 0.01

    def __post_init__(self):
        _, path = parse_gcs_uri(self.output_root.rstrip("/") + "/")
        if "presencial-vnext" not in path.split("/"):
            raise ValueError("Use an isolated output prefix containing /presencial-vnext/")
        if not self.allowed_buckets:
            raise ValueError("ALLOWED_INPUT_BUCKETS is required")
        if self.asr_backend != "canary":
            raise ValueError("Unsupported ASR backend")
        if self.diarization_backend not in ("nemotron", "none"):
            raise ValueError("Unsupported diarization backend")
        for value in (self.max_audio_seconds, self.max_audio_bytes, self.files_per_task,
                      self.max_conversations, self.window_seconds, self.max_prompt_chars,
                      self.targets_per_call, self.max_first_pass_calls):
            if type(value) is not int or value < 1:
                raise ValueError("Resource limits must be positive integers")
        if not 0 <= self.max_second_pass <= 20 or self.window_seconds > 30:
            raise ValueError("Invalid review/window budget")
        if self.speaker_min_seconds < 0 or not 0 <= self.speaker_min_ratio <= 1:
            raise ValueError("Invalid significant speaker policy")

    @classmethod
    def from_env(cls):
        from counter_presencial.isolation import Resources
        Resources(os.environ["GCP_PROJECT"]).validate_env()
        values = {
            "output_root": os.environ["PRESENCIAL_OUTPUT_GCS_ROOT"],
            "allowed_buckets": tuple(x.strip() for x in os.environ.get(
                "ALLOWED_INPUT_BUCKETS", "").split(",") if x.strip()),
        }
        mapping = {
            "project": "GCP_PROJECT", "bq_location": "BQ_LOCATION",
            "role_model": "PRESENCIAL_ROLE_MODEL", "canary_revision": "CANARY_REVISION",
            "nemotron_revision": "NEMOTRON_REVISION", "asr_backend": "ASR_BACKEND",
            "diarization_backend": "DIARIZATION_BACKEND",
        }
        for field, env in mapping.items():
            if env in os.environ:
                values[field] = os.environ[env]
        for field in ("max_audio_seconds", "max_audio_bytes", "files_per_task",
                      "max_conversations", "window_seconds", "max_prompt_chars",
                      "targets_per_call", "max_second_pass", "max_first_pass_calls"):
            if "PRESENCIAL_" + field.upper() in os.environ:
                values[field] = int(os.environ["PRESENCIAL_" + field.upper()])
        return cls(**values)

    def acoustic_spec(self):
        data = asdict(self)
        names = ("canary_model", "canary_revision", "nemotron_model", "nemotron_revision",
                 "asr_backend", "diarization_backend", "window_seconds", "max_audio_seconds")
        root = Path(__file__).resolve().parents[1]
        tracked = [root / "docker" / name for name in ("constraints-gpu.txt", "constraints-canary.txt")]
        tracked += [Path(__file__).with_name(name) for name in (
            "acoustics.py", "canary_worker.py", "canary_process.py", "saxo_canary_quality.py",
            "saxo_canary_nemo_timestamps.py", "saxo_nemotron_four.py", "saxo_turn_candidates.py")]
        signature = digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked})
        return {"version": VERSION, "runtime_and_processing_sha256": signature, **{k: data[k] for k in names},
                "speech_gate": "saxo_nemotron4_acoustic_activity_proxy",
                "speaker_min_seconds": self.speaker_min_seconds,
                "speaker_min_ratio": self.speaker_min_ratio,
                "runtime": "saxo-transformers-07338b6-canary-nemo-2.7.3-isolated",
                "precision": "float32_asr_bfloat16_diar"}

    def require_pinned_models(self):
        for name in ("canary", "nemotron"):
            enabled = self.asr_backend == name or self.diarization_backend == name
            if enabled and not re.fullmatch(r"[0-9a-f]{40}", getattr(self, name + "_revision")):
                raise ValueError(name + " requires an immutable 40-character model revision")
