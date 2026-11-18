# Adapted from Chemalog/saxo-stt-d, commit 63cbf230f76d17e6329a1efd908b69effc1faaaf.
# Source: app/presencial/canary_quality.py. Pure functions retained; no SAXO imports.
"""Conservative, auditable speech gating and repetition cleanup for SAXO Presencial.

No GPU, I/O, model, or production imports. Speaker acoustic activity is used as
a PROXY speech gate, not ground-truth VAD; timestamps are NEVER fabricated.
"""
from __future__ import annotations

import math
import re
from typing import Mapping

_WORD = re.compile(r"\w+(?:[’'-]\w+)*", re.UNICODE)


def remove_repetition_loop(text: str) -> tuple[str, dict]:
    """Detect runaway exact lexical loops, including malformed punctuation.

    We preserve the complete spoken prefix, first two copies of the loop, and
    any subsequent non-loop speech. Stray punctuation does not defeat the
    guard (Canary's hallucinations often end with dozens of '¿').
    Raw ASR is retained in a separate audit field and never overwritten.
    """
    source = (text or "").strip()
    matches = list(_WORD.finditer(source))
    words = [m.group().casefold() for m in matches]
    for start in range(len(words)):
        for size in range(1, 5):
            motif = words[start:start + size]
            if len(motif) < size:
                continue
            count = 1
            while (
                start + (count + 1) * size <= len(words)
                and words[
                    start + count * size:start + (count + 1) * size
                ] == motif
            ):
                count += 1
            if count < (8 if size <= 2 else 6) or count * size < 16:
                continue
            keep_end = matches[start + 2 * size - 1].end()
            run_end = matches[start + count * size - 1].end()
            remaining = words[start + count * size:]
            # If remaining lexical words are still mostly loop vocabulary,
            # treat the entire rest (including malformed punctuation) as
            # likely hallucination. Otherwise preserve later real speech.
            motif_vocabulary = set(motif)
            residual_ratio = (
                sum(w in motif_vocabulary for w in remaining) / len(remaining)
                if remaining else 1.0
            )
            if residual_ratio >= 0.80:
                clean = source[:keep_end].strip()
            else:
                clean = (source[:keep_end] + " " + source[run_end:].lstrip()).strip()
            return clean, {
                "loop_detected": True,
                "loop_unit_tokens": size,
                "loop_count": count,
                "discarded_tokens": len(words) - len(list(_WORD.finditer(clean))),
                "needs_review": True,
                "reason": "repetitive_asr_run",
                "subsequent_real_speech_preserved": residual_ratio < 0.80,
            }
    return source, {
        "loop_detected": False, "discarded_tokens": 0,
        "needs_review": False,
    }


def speech_windows(
    intervals: list[Mapping], audio_seconds: float, *,
    pad_seconds: float = 0.20,
    join_gap: float = 0.55,
    max_seconds: float = 24.0,
    min_seconds: float = 0.45,
) -> list[dict]:
    """Build speech-only windows from Nemotron speaker intervals.

    Overlapping speakers are treated as ONE speech-presence signal, never
    as separate model requests. Mark unavoidable hard splits for review.
    Empty spans yield [] (do not ask Canary to hallucinate silence).
    """
    values = [audio_seconds, pad_seconds, join_gap, max_seconds, min_seconds]
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values):
        raise ValueError("Invalid nonfinite speech-window parameters")
    if (audio_seconds <= 0 or pad_seconds < 0 or join_gap < 0
            or max_seconds < 5 or min_seconds <= 0 or min_seconds > max_seconds):
        raise ValueError("Invalid speech-window parameters")
    bounds = []
    for interval in intervals:
        if not isinstance(interval, Mapping):
            raise ValueError("Invalid acoustic interval")
        a, b = interval.get("start"), interval.get("end")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (a, b)):
            raise ValueError("Nonfinite speech interval")
        if a < 0 or b <= a or b > audio_seconds + 1.0:
            raise ValueError("Acoustic speech interval outside the source audio")
        a = max(0.0, float(a) - pad_seconds)
        b = min(float(audio_seconds), float(b) + pad_seconds)
        if b - a >= min_seconds:
            bounds.append((a, b))
    bounds.sort()
    merged: list[list[float]] = []
    for a, b in bounds:
        if merged and a - merged[-1][1] <= join_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    windows: list[dict] = []
    for a, b in merged:
        pos = a
        while pos < b - 1e-5:
            end = min(b, pos + max_seconds)
            if end - pos < min_seconds and windows:
                # Short remainder appended to last window only if safe.
                if windows[-1]["end"] == pos and b - windows[-1]["start"] <= max_seconds:
                    windows[-1]["end"] = round(b, 4)
                break
            windows.append({
                "start": round(pos, 4),
                "end": round(end, 4),
                "hard_split": end < b - 1e-5,
                "speech_gate": "nemotron4_acoustic_activity_proxy",
            })
            pos = end
    return windows


def alignment_quality(text: str, words: list[dict]) -> dict:
    """Diagnostic only. Do not assign fake time to unmatched words."""
    expected = len((text or "").split())
    valid = sum(
        isinstance(w, dict)
        and bool(str(w.get("word") or "").strip())
        and isinstance(w.get("start"), (int, float))
        and isinstance(w.get("end"), (int, float))
        and math.isfinite(w["start"])
        and math.isfinite(w["end"])
        and 0 <= w["start"] <= w["end"]
        for w in words
    )
    ratio = min(1.0, valid / expected) if expected else 0.0
    return {
        "input_words": expected,
        "timed_words": valid,
        "coverage_estimate": round(ratio, 4),
        "review_required": bool(expected and ratio < 0.80),
    }

