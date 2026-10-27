import hashlib
import json
import logging
import tempfile
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from worker.alignment import assign_words, group_words, to_text
from worker.audio import normalize
from worker.models import transcribe, diarize

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def processing_key(request, source, config):
    payload = {"recording_id": request.recording_id, "source": source, "models": config.as_dict()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def package_versions():
    values = {}
    for name in ("faster-whisper", "pyannote.audio", "torch", "torchaudio", "ctranslate2"):
        try:
            values[name] = version(name)
        except PackageNotFoundError:
            values[name] = "not-installed"
    return values


def run(repository, request, config, sink=None, max_seconds=7200,
        normalize_audio=normalize, transcribe_audio=transcribe, diarize_audio=diarize,
        catalog_metadata=None):
    key = processing_key(request, repository.source, config)
    attempt = uuid.uuid4().hex
    started, t0 = utc_now(), time.monotonic()
    with repository.lock(key):
        previous = repository.read_json(key, "completed.json")
        if previous and not request.force:
            result = repository.read_json(key, previous["result_path"])
            if result is None:
                raise RuntimeError("Manifest completo sin resultado; revisar integridad o usar force")
            if catalog_metadata:
                result["catalog"] = catalog_metadata
            if sink:
                sink.write(result, previous["result_uri"])
            repository.write_json(key, "status.json", {"status": "completed", "processing_key": key,
                                  "result_uri": previous["result_uri"], "updated_at": utc_now()})
            return {"status": "already_completed", "processing_key": key, "result_uri": previous["result_uri"]}
        status = {"processing_key": key, "recording_id": request.recording_id, "attempt_id": attempt,
                  "source": repository.source, "started_at": started}
        repository.write_json(key, "status.json", {**status, "status": "processing"})
        try:
            with tempfile.TemporaryDirectory(prefix="counter-") as tmp:
                source, wav = Path(tmp) / "input.audio", Path(tmp) / "normalized.wav"
                repository.download(source)
                duration = normalize_audio(source, wav, max_seconds=max_seconds)
                asr = transcribe_audio(wav, config)
                turns = diarize_audio(wav, config) if config.enable_diarization else {"regular": [], "exclusive": []}
                assigned = assign_words(asr["words"], turns["exclusive"], turns["regular"],
                                        config.min_overlap_ratio, config.ambiguity_margin)
                if not config.enable_diarization:
                    for word in assigned:
                        word.update(assignment_status="diarization_disabled", overlapping_speech=None,
                                    assignment_overlap_ratio=None)
                segments = group_words(assigned)
                if not config.enable_diarization:
                    for segment in segments:
                        segment["overlapping_speech"] = None
            result = {"schema_version": "1.0", "processing_key": key, "attempt_id": attempt,
                      "recording_id": request.recording_id, "source_system": "counter",
                      "source": repository.source, "processed_at": utc_now(),
                      "duration_seconds": duration, "elapsed_seconds": round(time.monotonic()-t0, 3),
                      "models": config.as_dict(), "packages": package_versions(),
                      "language": asr["language"], "language_probability": asr["language_probability"],
                      "transcription": segments, "diarization": turns if config.enable_diarization else None,
                      "diarization_status": "completed" if config.enable_diarization else "disabled",
                      "timestamp_gaps": asr["timestamp_gaps"],
                      "warnings": (["Speaker IDs válidos solo dentro de esta grabación; role no inferido.",
                                   "Diarización no separa fuentes ni garantiza recuperar frases simultáneas."]
                                   if config.enable_diarization else ["Solo transcripción: sin identificación de hablantes ni detección de solapamientos."]),
                      "serialized_level_1": to_text(segments), "catalog": catalog_metadata}
            json_path, text_path = f"attempts/{attempt}/result.json", f"attempts/{attempt}/transcript.txt"
            result_uri = repository.write_json(key, json_path, result, immutable=True)
            text_uri = repository.write_text(key, text_path, result["serialized_level_1"])
            if sink:
                sink.write(result, result_uri)
            # Publicar referencia solo después de persistir las salidas y sincronizar BQ si está habilitado.
            repository.write_json(key, "completed.json", {"result_path": json_path, "result_uri": result_uri,
                                  "text_uri": text_uri, "attempt_id": attempt, "updated_at": utc_now()})
            repository.write_json(key, "status.json", {**status, "status": "completed",
                                  "result_uri": result_uri, "updated_at": utc_now()})
            return {"status": "completed", "processing_key": key, "result_uri": result_uri}
        except Exception as exc:
            try:
                repository.write_json(key, "status.json", {**status, "status": "failed",
                                      "error_type": type(exc).__name__, "updated_at": utc_now()})
            except Exception:
                logger.exception("No se pudo guardar el estado de fallo")
            # No elimina ni sobrescribe el completed.json de un resultado anterior válido.
            raise
