"""Native Canary/CTC, Nemotron and conservative provider-neutral turn building."""
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
import re
import time

from counter_presencial.conversations import finite, validate_sample_offsets
from counter_presencial.saxo_turn_candidates import _split_tokens

VALID_TIMESTAMPS = {"native_ctc_verified", "baseline_word_timestamps"}

def lexical(text):
    return re.findall(r"\w+", text.casefold())


def native_words(text, stamps, start, end):
    """Fail closed on mismatches; retain the whole untimed transcript upstream."""
    if not isinstance(stamps, list) or not stamps:
        return [], "missing_native_timestamps"
    words, previous = [], -1.0
    for stamp in stamps:
        if not isinstance(stamp, dict):
            return [], "invalid_native_timestamps"
        a, b, word = stamp.get("start"), stamp.get("end"), stamp.get("word")
        if (not finite(a) or not finite(b) or not 0 <= a < b <= end - start + 0.05
                or a >= end - start
                or a < previous or not isinstance(word, str) or not word.strip()):
            return [], "invalid_native_timestamps"
        previous = a
        words.append({"word": word.strip(), "start": start + a,
                      "end": start + min(b, end - start)})
    if lexical(" ".join(w["word"] for w in words)) != lexical(text):
        return [], "native_words_do_not_match_text"
    return words, "native_ctc_verified"


def make_turns(chunks, max_words=35, pause=0.85):
    turns = []

    def add(text, words, quality, chunk_index, boundary="unaligned_text_preserved"):
        turns.append({"turn_id": f"T{len(turns) + 1:05d}", "text": text,
                      "start": words[0]["start"] if words else None,
                      "end": words[-1]["end"] if words else None,
                      "words": words, "source_quality_review": quality not in VALID_TIMESTAMPS,
                      "source_text_review": quality in ("repetition_suspected", "text_changed_by_repetition_cleanup"),
                      "timestamp_status": quality, "source_window_index": chunk_index,
                      "word_count": len(words) if words else len(text.split()),
                      "boundary_reason": boundary})

    for index, chunk in enumerate(chunks):
        if not chunk["text"].strip():
            continue
        words = chunk.get("words", [])
        if not words or chunk["timestamp_status"] not in VALID_TIMESTAMPS:
            add(chunk["text"], [], chunk["timestamp_status"], index)
            continue
        offset = 0
        for candidate in _split_tokens(words, pause_seconds=pause, max_words=max_words):
            selected = words[offset:offset + candidate["word_count"]]
            offset += candidate["word_count"]
            add(candidate["text"], selected, chunk["timestamp_status"], index,
                candidate["boundary_reason"])
    return turns


class Models:
    """Sequential GPU phases, separate Python environments, no silent fallback."""
    def __init__(self, config):
        self.config, self.nemotron, self.processor = config, None, None
        self.canary = None
        self.diarization_diagnostics = {}
        self.canary_runtime = {}

    def load(self):
        # Backward-compatible diagnostic entry point; never loads both runtimes.
        self.load_diarizer()

    def load_diarizer(self):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("Presencial acoustic job requires CUDA")
        self.config.require_pinned_models()
        self.close_canary()
        if self.config.diarization_backend == "nemotron" and self.nemotron is None:
            from transformers import AutoModelForAudioFrameClassification, AutoProcessor
            kwargs = {"revision": self.config.nemotron_revision}
            self.processor = AutoProcessor.from_pretrained(self.config.nemotron_model, **kwargs)
            self.nemotron = AutoModelForAudioFrameClassification.from_pretrained(
                self.config.nemotron_model, **kwargs).to(device="cuda", dtype=torch.bfloat16).eval()

    def unload_diarizer(self):
        loaded = self.nemotron is not None
        self.nemotron = self.processor = None
        if loaded:
            import gc
            import torch
            gc.collect()
            torch.cuda.empty_cache()

    def close_canary(self):
        if self.canary is not None:
            self.canary.close()
            self.canary = None

    def load_canary(self):
        self.unload_diarizer()
        if self.canary is None:
            from counter_presencial.canary_process import CanaryProcess
            self.canary = CanaryProcess(self.config)
            self.canary.load()
            self.canary_runtime = dict(self.canary.runtime)

    def close(self):
        self.close_canary()
        self.unload_diarizer()

    def diarize(self, signal):
        import torch
        from counter_presencial.saxo_nemotron_four import extract_four_speaker_timeline
        if self.config.diarization_backend == "none":
            return []
        self.load_diarizer()
        inputs = self.processor(signal, sampling_rate=16000).to("cuda", dtype=self.nemotron.dtype)
        with torch.inference_mode():
            logits = self.nemotron(**inputs).logits
        raw = self.processor.extract_speaker_dict(logits, getattr(inputs, "attention_mask", None))[0]
        if not raw:
            self.diarization_diagnostics = {"nemotron_significant_speaker_count": 0,
                                             "nemotron_presencial_cap": 4, "no_speech": True}
            return []
        try:
            rows, self.diarization_diagnostics = extract_four_speaker_timeline(
                raw, audio_seconds=len(signal) / 16000,
                min_seconds=self.config.speaker_min_seconds, min_ratio=self.config.speaker_min_ratio)
        except ValueError as exc:
            if str(exc) != "No significant Nemotron acoustic channels":
                raise
            self.diarization_diagnostics = {"nemotron_significant_speaker_count": 0,
                                             "nemotron_presencial_cap": 4, "no_speech": True}
            return []
        if any(row["end"] > len(signal) / 16000 + .1 for row in rows):
            raise ValueError("Nemotron interval exceeds original audio")
        return [{"start": row["start"], "end": row["end"], "speaker_id": row["speaker"]}
                for row in rows]

    def transcribe(self, path):
        self.load_canary()
        return self.canary.transcribe(path)

def process_audio(conversation, config, store, models, temporary, checkpoint=None):
    import numpy as np
    import soundfile as sf
    import torch
    from counter_presencial.audio import normalize
    from counter_presencial.saxo_canary_quality import speech_windows, remove_repetition_loop
    from counter_presencial.saxo_canary_nemo_timestamps import normalize_native_words
    began = time.monotonic()
    parts, arrays, lengths = conversation["parts"], [], []
    timings = {"download_seconds": 0., "normalize_seconds": 0.}
    if sum(p["size_bytes"] for p in parts) > config.max_audio_bytes:
        raise ValueError("Conversation exceeds compressed input size budget")
    for index, part in enumerate(parts):
        src, wav = temporary / f"source{index}", temporary / f"part{index}.wav"
        stage = time.monotonic()
        store.download(part, src)
        timings["download_seconds"] += time.monotonic() - stage
        stage = time.monotonic()
        normalize(src, wav, max_seconds=config.max_audio_seconds)
        timings["normalize_seconds"] += time.monotonic() - stage
        signal, rate = sf.read(wav, dtype="float32")
        if rate != 16000 or signal.ndim != 1:
            raise ValueError("Expected normalized mono 16 kHz audio")
        lengths.append(len(signal))
        if sum(lengths) / rate > config.max_audio_seconds:
            raise ValueError("Conversation exceeds duration budget")
        arrays.append(signal)
        src.unlink()
        wav.unlink()
    offsets = validate_sample_offsets(parts, lengths)
    signal = np.concatenate(arrays)
    del arrays
    torch.cuda.reset_peak_memory_stats()
    stage = time.monotonic()
    saved = checkpoint("get", "diarization", None) if checkpoint else None
    if saved is None:
        timeline = models.diarize(signal)
        torch.cuda.synchronize()
        saved = {"timeline": timeline, "diagnostics": dict(models.diarization_diagnostics)}
        if checkpoint:
            checkpoint("put", "diarization", saved)
    else:
        timeline = saved["timeline"]
    timings["diarize_seconds"] = time.monotonic() - stage
    diar_peak = torch.cuda.max_memory_allocated()
    # SAXO runs Canary in its own environment after saving Nemotron's timeline.
    models.unload_diarizer()
    duration = len(signal) / 16000
    intervals = timeline if config.diarization_backend == "nemotron" else [
        {"start": 0., "end": duration}]
    windows = speech_windows(intervals, duration, max_seconds=config.window_seconds)
    timings["transcribe_seconds"] = 0.
    chunks, canary_peak = [], 0
    for index, span in enumerate(windows):
        start, end = span["start"], span["end"]
        wav = temporary / "window.wav"
        sf.write(wav, signal[round(start * 16000):round(end * 16000)], 16000, subtype="PCM_16")
        stage = time.monotonic()
        result = checkpoint("get", f"window-{index:06}", None) if checkpoint else None
        if result is None:
            result = models.transcribe(wav)
            if checkpoint:
                checkpoint("put", f"window-{index:06}", result)
        timings["transcribe_seconds"] += time.monotonic() - stage
        if result.get("runtime"):
            models.canary_runtime = result["runtime"]
        raw = result["text"].strip()
        text, cleaning = remove_repetition_loop(raw)
        words, status = normalize_native_words(
            result.get("native_stamps"), cleaned_text=text, original_text=raw,
            window_start=start, window_end=end)
        if not text:
            continue
        canary_peak = max(canary_peak, result.get("peak_vram_bytes", 0))
        chunks.append({"start": start, "end": end, "text": text, "raw_text": raw,
                       "words": words, "timestamp_status": status, "cleaning": cleaning,
                       "hard_split": span["hard_split"], "speech_gate": span["speech_gate"]})
    speakers = sorted({t["speaker_id"] for t in timeline})
    timings["acoustic_seconds"] = time.monotonic() - began
    packages = {}
    for name in ("transformers", "torch", "huggingface-hub"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not-installed"
    # No concurrent GPU models: the peak is the maximum of the sequential phases.
    return {"schema_version": "2.0", "conversation_id": conversation["conversation_id"],
            "source_parts": offsets, "duration_seconds": duration,
            "models": config.acoustic_spec(), "diarization": timeline,
            "diarization_diagnostics": saved["diagnostics"],
            "speech_windows": windows, "speech_window_seconds": sum(w["end"] - w["start"] for w in windows),
            "skip_reason": "no_significant_speech" if not windows else None,
            "speaker_ids": speakers, "speaker_count_exceeds_presencial_cap": False,
            "speaker_scope": "conversation", "timestamp_scope": "conversation",
            "word_alignment": "canary_nemo_ctc_native", "chunks": chunks,
            "transcription": make_turns(chunks), "timings": timings,
            "packages": packages, "canary_runtime": dict(models.canary_runtime),
            "timing_note": "Stage wall times include model loading; cached windows skip inference",
            "accelerator": torch.cuda.get_device_name(0), "cuda_version": torch.version.cuda,
            "peak_vram_bytes": max(diar_peak, canary_peak),
            "peak_vram_by_phase": {"nemotron": diar_peak, "canary": canary_peak},
            "quality_status": "review_required" if any(c["timestamp_status"] not in VALID_TIMESTAMPS
                                                        for c in chunks) else "unvalidated"}
