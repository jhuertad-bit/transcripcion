import os
import time
from functools import lru_cache


@lru_cache(maxsize=1)
def whisper_model(model_id, device, compute_type, cpu_threads):
    from faster_whisper import WhisperModel
    bundled = os.environ.get("BUNDLED_WHISPER_MODEL")
    local = os.environ.get("WHISPER_LOCAL_PATH", "/opt/models/whisper")
    use_local = model_id == bundled and os.path.isfile(os.path.join(local, "model.bin"))
    return WhisperModel(local if use_local else model_id, device=device,
                        compute_type=compute_type, cpu_threads=cpu_threads,
                        local_files_only=use_local)


def transcribe(path, config):
    started = time.monotonic()
    hits = whisper_model.cache_info().hits
    model = whisper_model(config.whisper_model, config.device, config.compute_type, config.cpu_threads)
    load_seconds = time.monotonic() - started
    reused = whisper_model.cache_info().hits > hits
    started = time.monotonic()
    segments, info = model.transcribe(str(path), language=config.language, word_timestamps=True,
                                     vad_filter=True, initial_prompt=config.initial_prompt)
    words, timestamp_gaps = [], []
    for seg in segments:
        if seg.text.strip() and not seg.words:
            timestamp_gaps.append({"start": seg.start, "end": seg.end, "text": seg.text,
                                   "reason": "no_word_timestamps"})
        for word in seg.words or []:
            if not word.word.strip():
                continue
            if word.end <= word.start:
                timestamp_gaps.append({"start": word.start, "end": word.end, "text": word.word,
                                       "reason": "invalid_word_interval"})
                continue
            words.append({"word": word.word, "start": word.start, "end": word.end,
                          "asr_probability": word.probability})
    result = {"words": words, "timestamp_gaps": timestamp_gaps,
              "language": info.language, "language_probability": info.language_probability,
              "timings": {"whisper_model_load_seconds": round(load_seconds, 3),
                          "whisper_inference_seconds": round(time.monotonic() - started, 3)},
              "model_reused": reused}
    return result


def diarize(path, config):
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline
    # Ruta local opcional en DIARIZATION_MODEL; los modelos remotos gated necesitan HF_TOKEN.
    kwargs = {"token": os.environ["HF_TOKEN"]} if os.environ.get("HF_TOKEN") else {}
    pipeline = Pipeline.from_pretrained(config.diarization_model, **kwargs)
    pipeline.to(torch.device(config.device))
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    output = pipeline({"waveform": torch.from_numpy(data.T.copy()), "sample_rate": rate},
                      min_speakers=config.min_speakers, max_speakers=config.max_speakers)

    def turns(annotation):
        return sorted([{"speaker_id": str(speaker), "start": float(turn.start), "end": float(turn.end)}
                       for turn, speaker in annotation], key=lambda x: (x["start"], x["end"]))

    return {"regular": turns(output.speaker_diarization),
            "exclusive": turns(output.exclusive_speaker_diarization)}
