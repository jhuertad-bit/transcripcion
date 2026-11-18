# Adapted from Chemalog/saxo-stt-d, commit 63cbf230f76d17e6329a1efd908b69effc1faaaf.
# Source: app/presencial/nemotron_four.py. Pure functions retained; no SAXO imports.
"""SAXO significant-channel policy without its production backend imports."""
from __future__ import annotations

import math

from numbers import Integral, Real

from typing import Any

MAX_PRESENCIAL_SPEAKERS = 4

def extract_four_speaker_timeline(
    raw_segments: list[dict[str, Any]],
    *,
    audio_seconds: float,
    min_seconds: float = 1.0,
    min_ratio: float = 0.01,
) -> tuple[list[dict], dict]:
    """Convert model speaker-channel segments into a <=4 acoustic timeline.

    Uses the *same* significance threshold and first-appearance ordering
    as the existing Nemotron backend. Does not merge or truncate a fifth
    significant channel: that is a hard error needing manual inspection.
    """
    if not math.isfinite(audio_seconds) or audio_seconds <= 0:
        raise ValueError("Expected positive finite audio duration")
    if min_seconds < 0 or min_ratio < 0:
        raise ValueError("Negative significance threshold")

    activity: dict[int, float] = {}
    first_seen: dict[int, float] = {}
    cleaned: list[dict] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            raise ValueError("Malformed speaker segment")
        channel = item.get("Speaker")
        start, end = item.get("Start"), item.get("End")
        if (
            not isinstance(channel, Integral)
            or isinstance(channel, bool)
            or not 0 <= channel < 8
        ):
            raise ValueError("Unexpected Nemotron speaker channel")
        channel = int(channel)
        if any(
            not isinstance(v, Real)
            or isinstance(v, bool)
            or not math.isfinite(v)
            for v in (start, end)
        ):
            raise ValueError("Nonfinite speaker interval")
        if start < 0 or end <= start:
            raise ValueError("Invalid speaker interval")
        start, end = float(start), float(end)
        cleaned.append({"channel": channel, "start": start, "end": end})
        activity[channel] = activity.get(channel, 0.0) + end - start
        first_seen[channel] = min(start, first_seen.get(channel, start))

    threshold = max(min_seconds, audio_seconds * min_ratio)
    significant = sorted(
        (ch for ch, seconds in activity.items() if seconds >= threshold),
        key=lambda ch: (first_seen[ch], ch),
    )
    if not significant:
        raise ValueError("No significant Nemotron acoustic channels")
    if len(significant) > MAX_PRESENCIAL_SPEAKERS:
        raise ValueError(
            "Nemotron found %d significant speaker channels; presencial "
            "cap is 4. Refusing to discard the additional speaker."
            % len(significant)
        )
    mapping = {original: rank for rank, original in enumerate(significant)}
    timeline = [
        {
            "start": item["start"],
            "end": item["end"],
            "speaker": f"SPEAKER_{mapping[item['channel']]:02d}",
        }
        for item in cleaned if item["channel"] in mapping
    ]
    timeline.sort(key=lambda r: (r["start"], r["end"], r["speaker"]))
    diagnostics = {
        "nemotron_max_speakers": 8,
        "nemotron_expected_speakers": MAX_PRESENCIAL_SPEAKERS,
        "nemotron_output_speaker_channels": len(significant),
        "nemotron_all_speaker_activity_seconds": {
            f"SPEAKER_{channel:02d}": round(seconds, 6)
            for channel, seconds in sorted(activity.items())
        },
        "nemotron_significant_speakers": [
            f"SPEAKER_{channel:02d}" for channel in significant
        ],
        "nemotron_significant_speaker_count": len(significant),
        "nemotron_speaker_channel_map": {
            f"SPEAKER_{original:02d}": f"SPEAKER_{rank:02d}"
            for original, rank in mapping.items()
        },
        "nemotron_significant_threshold_seconds": round(threshold, 6),
        "nemotron_presencial_cap": MAX_PRESENCIAL_SPEAKERS,
        "nemotron_discarded_insignificant_channels": [
            f"SPEAKER_{channel:02d}" for channel in sorted(activity)
            if channel not in mapping
        ],
    }
    return timeline, diagnostics
