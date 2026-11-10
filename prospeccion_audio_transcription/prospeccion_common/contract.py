import hashlib
import re
from dataclasses import dataclass


def parse_gcs_uri(uri):
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        raise ValueError("audio_uri debe ser gs://bucket/objeto")
    bucket, sep, name = uri[5:].partition("/")
    if not sep or not name or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
        raise ValueError("URI GCS inválida: se requiere bucket y objeto")
    if any(ord(c) < 32 for c in uri):
        raise ValueError("URI con caracteres de control")
    return bucket, name


@dataclass(frozen=True)
class AudioRequest:
    audio_uri: str
    recording_id: str
    force: bool = False

    @classmethod
    def from_dict(cls, body, allowed_buckets=()):
        if not isinstance(body, dict):
            raise ValueError("Se requiere un objeto JSON")
        extra = set(body) - {"audio_uri", "recording_id", "force"}
        if extra:
            raise ValueError("Campos no admitidos: " + ", ".join(sorted(extra)))
        uri = body.get("audio_uri")
        bucket, _ = parse_gcs_uri(uri)
        if allowed_buckets and bucket not in allowed_buckets:
            raise ValueError("Bucket de entrada no permitido")
        rid = body.get("recording_id", hashlib.sha256(uri.encode()).hexdigest()[:24])
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", rid):
            raise ValueError("recording_id: 1–128 letras, números, guion, punto o subrayado")
        force = body.get("force", False)
        if type(force) is not bool:
            raise ValueError("force debe ser booleano")
        return cls(uri, rid, force)

    def job_environment(self):
        return {"AUDIO_URI": self.audio_uri, "RECORDING_ID": self.recording_id,
                "FORCE": str(self.force).lower()}
