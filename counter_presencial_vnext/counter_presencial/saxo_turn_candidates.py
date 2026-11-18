"""SAXO Presencial candidate boundaries; no model-inferred voices or timestamps.

Copied pure functions from Chemalog/saxo-stt-d app/presencial/pilot.py,
commit 63cbf230f76d17e6329a1efd908b69effc1faaaf. Runtime-independent.
The Canary pipeline applies these functions per source window, as SAXO does.
"""
from __future__ import annotations

import re
from typing import List, Mapping, Sequence, Any

END_PUNCTUATION = re.compile(r'[.!?…。！？]["”’)]*$')


def _make_candidate(tokens: List[Mapping[str, Any]], boundary: str, index: int) -> dict:
    return {
        "turn_id": "T%04d" % index,
        "start": tokens[0].get("start"),
        "end": tokens[-1].get("end"),
        "text": " ".join(item["word"] for item in tokens),
        "word_count": len(tokens),
        "boundary_reason": boundary,
    }

def _split_tokens(
    tokens: Sequence[Mapping[str, Any]],
    *,
    pause_seconds: float,
    max_words: int,
) -> List[dict]:
    if pause_seconds <= 0 or max_words <= 0:
        raise ValueError("pause_seconds and max_words must be positive")

    result: List[dict] = []
    pending: List[Mapping[str, Any]] = []

    def flush(reason: str) -> None:
        if pending:
            result.append(_make_candidate(pending, reason, len(result) + 1))
            pending.clear()

    for word in tokens:
        if pending:
            previous_end = pending[-1].get("end")
            current_start = word.get("start")
            if (
                previous_end is not None
                and current_start is not None
                and current_start - previous_end >= pause_seconds
            ):
                flush("pause")
        pending.append(word)
        if END_PUNCTUATION.search(str(word["word"])):
            flush("punctuation")
        elif len(pending) >= max_words:
            flush("max_words")
    flush("end_of_input")
    return result
