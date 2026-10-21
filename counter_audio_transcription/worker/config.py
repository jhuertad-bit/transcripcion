from dataclasses import dataclass, asdict
import os


def boolean(value):
    if value.lower() not in ("true", "false"):
        raise ValueError("Booleanos de configuración: true o false")
    return value.lower() == "true"


@dataclass(frozen=True)
class ModelConfig:
    whisper_model: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    diarization_model: str = "pyannote/speaker-diarization-community-1"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "es"
    initial_prompt: str = "Atención presencial en español de Perú sobre carreras, admisión y matrícula universitaria."
    min_speakers: int = 1
    max_speakers: int = 6
    min_overlap_ratio: float = 0.5
    ambiguity_margin: float = 0.1
    pipeline_version: str = "counter-v0.1.0"

    def __post_init__(self):
        if not 1 <= self.min_speakers <= self.max_speakers:
            raise ValueError("Rango de hablantes inválido")
        if not 0 < self.min_overlap_ratio <= 1 or not 0 <= self.ambiguity_margin <= 1:
            raise ValueError("Umbrales de asignación fuera de rango")

    @classmethod
    def from_env(cls):
        return cls(**{name: cast(os.environ[env]) for name, env, cast in [
            ("whisper_model", "WHISPER_MODEL", str), ("diarization_model", "DIARIZATION_MODEL", str),
            ("device", "MODEL_DEVICE", str), ("compute_type", "WHISPER_COMPUTE_TYPE", str),
            ("language", "LANGUAGE", str), ("initial_prompt", "INITIAL_PROMPT", str),
            ("min_speakers", "MIN_SPEAKERS", int), ("max_speakers", "MAX_SPEAKERS", int),
            ("min_overlap_ratio", "MIN_OVERLAP_RATIO", float),
            ("ambiguity_margin", "AMBIGUITY_MARGIN", float), ("pipeline_version", "PIPELINE_VERSION", str)
        ] if env in os.environ})

    def as_dict(self):
        return asdict(self)
