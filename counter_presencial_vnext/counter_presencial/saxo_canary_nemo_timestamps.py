# Adapted from Chemalog/saxo-stt-d, commit 63cbf230f76d17e6329a1efd908b69effc1faaaf.
# Source: app/presencial/canary_nemo_timestamps.py. Pure functions retained; no SAXO imports.
"""Pure helpers for Canary NeMo native CTC word timestamps.

Never infer a word timestamp from its enclosing speech window. Mismatched,
missing, or invalid native timestamps remain unaligned for human review.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from typing import Any

_TOKEN = re.compile(r"\w+", flags=re.UNICODE)


def _lexical(text: str) -> list[str]:
    return [item.casefold() for item in _TOKEN.findall(text or "")]


def normalize_native_words(
    stamps: Any,
    *,
    cleaned_text: str,
    original_text: str,
    window_start: float,
    window_end: float,
) -> tuple[list[dict], str]:
    """Return absolute word timestamps only if native evidence is consistent.

    Canary's CTC auxiliary produces *relative* positions in the cropped audio.
    If repetition cleanup edited text or word alignment differs, do not attach
    timestamps to the wrong lexical content. Keep that text, but flag it.
    """
    if not (math.isfinite(window_start) and math.isfinite(window_end)):
        raise ValueError("Nonfinite speech window")
    if window_start < 0 or window_end <= window_start:
        raise ValueError("Invalid speech window")
    if not isinstance(stamps, list) or not stamps:
        return [], "missing_native_word_timestamps"
    if cleaned_text != original_text:
        return [], "text_changed_by_repetition_cleanup"

    duration = window_end - window_start
    words = []
    prev_start = -1.0
    for item in stamps:
        if not isinstance(item, dict):
            return [], "invalid_native_word_timestamp"
        word = item.get("word")
        start, end = item.get("start"), item.get("end")
        if (
            not isinstance(word, str) or not word.strip()
            or not isinstance(start, (float, int)) or isinstance(start, bool)
            or not isinstance(end, (float, int)) or isinstance(end, bool)
            or not math.isfinite(start) or not math.isfinite(end)
            or start < 0 or end < start or end > duration + 0.05
            or start + 1e-4 < prev_start
        ):
            return [], "invalid_native_word_timestamp"
        prev_start = float(start)
        words.append({
            "word": word.strip(),
            "start": round(window_start + float(start), 4),
            "end": round(window_start + min(float(end), duration), 4),
        })

    if _lexical(" ".join(w["word"] for w in words)) != _lexical(cleaned_text):
        return [], "native_words_do_not_match_transcript"
    return words, "native_ctc_verified"


def transcript_similarity(baseline: str, candidate: str) -> float:
    """Diagnostic only: differences in NeMo vs Transformers text are expected."""
    return round(
        SequenceMatcher(None, _lexical(baseline), _lexical(candidate),
                        autojunk=False).ratio(),
        4,
    )


def compare_timestamp_pairs(baseline: dict, candidate: dict) -> dict:
    """Compare timing disagreement only where both ASR lexical outputs match.

    Disagreement is NOT alignment accuracy; no human reference is available.
    """
    left, right = baseline.get("words") or [], candidate.get("words") or []
    if (
        _lexical(baseline.get("text", "")) != _lexical(candidate.get("text", ""))
        or len(left) != len(right) or not left
        or any(_lexical(a.get("word", "")) != _lexical(b.get("word", ""))
               for a, b in zip(left, right))
    ):
        return {"comparable_words": 0, "status": "not_comparable_text_or_words"}
    starts, ends = [], []
    for a, b in zip(left, right):
        try:
            start = abs(float(a["start"]) - float(b["start"]))
            end = abs(float(a["end"]) - float(b["end"]))
        except (KeyError, TypeError, ValueError):
            return {"comparable_words": 0, "status": "invalid_time"}
        if not (math.isfinite(start) and math.isfinite(end)):
            return {"comparable_words": 0, "status": "invalid_time"}
        starts.append(start)
        ends.append(end)
    return {
        "comparable_words": len(starts),
        "status": "comparable_not_ground_truth",
        "mean_abs_start_diff_seconds": round(sum(starts) / len(starts), 4),
        "mean_abs_end_diff_seconds": round(sum(ends) / len(ends), 4),
    }

