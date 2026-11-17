"""Native Canary/CTC, Nemotron and conservative provider-neutral turn building."""
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
import re
import time

from counter_presencial.conversations import finite, validate_sample_offsets

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

    def add(text, words, quality, chunk_index):
        turns.append({"turn_id": f"T{len(turns) + 1:05d}", "text": text,
                      "start": words[0]["start"] if words else None,
                      "end": words[-1]["end"] if words else None,
                      "words": words, "source_quality_review": quality not in VALID_TIMESTAMPS,
                      "source_text_review": quality == "repetition_suspected",
                      "timestamp_status": quality, "source_window_index": chunk_index})

    for index, chunk in enumerate(chunks):
        if not chunk["text"].strip():
            continue
        words = chunk.get("words", [])
        if not words or chunk["timestamp_status"] not in VALID_TIMESTAMPS:
            add(chunk["text"], [], chunk["timestamp_status"], index)
            continue
        pending = []
        for word in words:
            if pending and word["start"] - pending[-1]["end"] >= pause:
                add(" ".join(w["word"] for w in pending), pending, chunk["timestamp_status"], index)
                pending = []
            pending.append(word)
            if re.search(r'[.!?…]["”\')]*$', word["word"]) or len(pending) >= max_words:
                add(" ".join(w["word"] for w in pending), pending, chunk["timestamp_status"], index)
                pending = []
        if pending:
            add(" ".join(w["word"] for w in pending), pending, chunk["timestamp_status"], index)
    return turns


class Models:
    """One instance per task: no model load per audio; no silent CPU fallback."""
    def __init__(self, config):
        self.config, self.canary, self.nemotron, self.processor = config, None, None, None

    def load(self):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("Presencial acoustic job requires CUDA")
        self.config.require_pinned_models()
        if self.config.diarization_backend == "nemotron" and self.nemotron is None:
            from transformers import AutoModelForAudioFrameClassification, AutoProcessor
            kwargs = {"revision": self.config.nemotron_revision}
            self.processor = AutoProcessor.from_pretrained(self.config.nemotron_model, **kwargs)
            self.nemotron = AutoModelForAudioFrameClassification.from_pretrained(
                self.config.nemotron_model, **kwargs).to(device="cuda", dtype=torch.bfloat16).eval()
        if self.config.asr_backend == "canary" and self.canary is None:
            from huggingface_hub import snapshot_download
            from nemo.collections.asr.models import ASRModel
            folder = snapshot_download(self.config.canary_model, revision=self.config.canary_revision,
                                       allow_patterns=["*.nemo"])
            files = list(Path(folder).glob("*.nemo"))
            if len(files) != 1:
                raise RuntimeError("Expected exactly one pinned Canary .nemo checkpoint")
            self.canary = ASRModel.restore_from(str(files[0])).to(device="cuda", dtype=torch.float32).eval()
        if self.config.asr_backend == "canary":
            if getattr(self.canary, "timestamps_asr_model", None) is None:
                raise RuntimeError("Canary checkpoint lacks its auxiliary CTC timestamp model")

    def diarize(self, signal):
        import torch
        if self.config.diarization_backend == "none":
            return []
        inputs = self.processor(signal, sampling_rate=16000).to("cuda", dtype=self.nemotron.dtype)
        with torch.inference_mode():
            logits = self.nemotron(**inputs).logits
        raw = self.processor.extract_speaker_dict(logits, getattr(inputs, "attention_mask", None))[0]
        timeline = []
        for item in raw:
            start, end, channel = item["Start"], item["End"], item["Speaker"]
            if not finite(start) or not finite(end) or not 0 <= start < end <= len(signal) / 16000 + .1:
                raise ValueError("Invalid Nemotron interval")
            if isinstance(channel, bool) or int(channel) != channel or not 0 <= int(channel) < 8:
                raise ValueError("Unexpected Nemotron speaker channel")
            timeline.append({"start": float(start), "end": float(end),
                             "speaker_id": f"SPEAKER_{int(channel):02d}"})
        # Preserve ALL channels, including short speakers; cap=4 is a diagnostic.
        return sorted(timeline, key=lambda x: (x["start"], x["end"]))

    def transcribe(self, path):
        import torch
        with torch.inference_mode():
            out = self.canary.transcribe([str(path)], source_lang="es", target_lang="es", timestamps=True)
        if not out or not hasattr(out[0], "timestamp"):
            raise RuntimeError("NeMo runtime does not expose native Canary timestamps")
        stamps = out[0].timestamp
        return {"text": str(out[0].text or ""),
                "native_stamps": stamps.get("word") if isinstance(stamps, dict) else None}


def process_audio(conversation, config, store, models, temporary):
    import numpy as np
    import soundfile as sf
    import torch
    from counter_presencial.audio import normalize
    began = time.monotonic()
    models.load()
    load_seconds = time.monotonic() - began
    parts, arrays, lengths = conversation["parts"], [], []
    timings = {"model_load_seconds": load_seconds, "download_seconds": 0., "normalize_seconds": 0.}
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
    timeline = models.diarize(signal)
    torch.cuda.synchronize()
    timings["diarize_seconds"] = time.monotonic() - stage
    timings["transcribe_seconds"] = 0.
    chunks = []
    # Full-audio coverage is intentional: a missed Nemotron voice cannot erase ASR.
    # Acoustic speech gating is deferred until omission recall has been measured.
    for position in range(0, len(signal), config.window_seconds * 16000):
        end_sample = min(len(signal), position + config.window_seconds * 16000)
        start, end = position / 16000, end_sample / 16000
        # Context padding protects syllables at hard cuts. Ownership is determined
        # by the midpoint of native aligned words, never by invented timestamps.
        crop_start, crop_end = max(0, position - 6400), min(len(signal), end_sample + 6400)
        wav = temporary / "window.wav"
        sf.write(wav, signal[crop_start:crop_end], 16000, subtype="PCM_16")
        stage = time.monotonic()
        result = models.transcribe(wav)
        torch.cuda.synchronize()
        timings["transcribe_seconds"] += time.monotonic() - stage
        raw_text = text = result["text"].strip()
        if "native_stamps" in result:
            words, status = native_words(text, result["native_stamps"], crop_start / 16000, crop_end / 16000)
        else:
            words = [{**w, "start": w["start"] + crop_start / 16000, "end": w["end"] + crop_start / 16000}
                     for w in result["words"]]
            status = "baseline_word_timestamps"
        if words:
            words = [w for w in words if start <= (w["start"] + w["end"]) / 2 < end]
            text = " ".join(w["word"].strip() for w in words)
        tokens = lexical(text)
        repeat = len(tokens) > 15 and len(set(tokens)) <= 2
        if repeat:
            status = "repetition_suspected"
        chunks.append({"start": start, "end": end, "text": text, "raw_text": raw_text,
                       "crop_start": crop_start / 16000, "crop_end": crop_end / 16000,
                       "words": words, "timestamp_status": status, "hard_split": end_sample < len(signal)})
    speakers = sorted({t["speaker_id"] for t in timeline})
    timings["acoustic_seconds"] = time.monotonic() - began
    packages = {}
    for name in ("nemo_toolkit", "transformers", "torch", "huggingface-hub"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "not-installed"
    return {"schema_version": "2.0", "conversation_id": conversation["conversation_id"],
            "source_parts": offsets, "duration_seconds": len(signal) / 16000,
            "models": config.acoustic_spec(), "diarization": timeline,
            "speaker_ids": speakers, "speaker_count_exceeds_presencial_cap": len(speakers) > 4,
            "speaker_scope": "conversation", "timestamp_scope": "conversation",
            "chunks": chunks, "transcription": make_turns(chunks),
            "timings": timings, "packages": packages,
            "accelerator": torch.cuda.get_device_name(0), "cuda_version": torch.version.cuda,
            "peak_vram_bytes": torch.cuda.max_memory_allocated(),
            "quality_status": "review_required" if any(c["timestamp_status"] not in VALID_TIMESTAMPS
                                                        for c in chunks) or len(speakers) > 4 else "unvalidated"}
