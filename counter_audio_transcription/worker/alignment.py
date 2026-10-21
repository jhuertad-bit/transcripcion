"""Asociación temporal: no identifica personas ni infiere roles comerciales."""
from collections import defaultdict
import math


def overlap(a, b, c, d):
    return max(0.0, min(b, d) - max(a, c))


def valid_interval(start, end):
    return math.isfinite(start) and math.isfinite(end) and 0 <= start < end


def assign_words(words, exclusive_turns, regular_turns, min_ratio=0.5, margin=0.1):
    """Exclusive asigna voz; regular conserva las marcas de habla simultánea.

    El score temporal no es una probabilidad ni confianza del modelo acústico.
    Sin suficiente intersección, o ante empate, se preserva speaker_id=None.
    """
    assigned = []
    for word in sorted(words, key=lambda w: (w["start"], w["end"])):
        start, end = float(word["start"]), float(word["end"])
        if not valid_interval(start, end):
            raise ValueError("Timestamp de palabra inválido")
        scores = defaultdict(float)
        for turn in exclusive_turns:
            scores[turn["speaker_id"]] += overlap(start, end, turn["start"], turn["end"])
        ranked = sorted(((s, v / (end-start)) for s, v in scores.items() if v > 0),
                        key=lambda x: (-x[1], x[0]))
        best = ranked[0][1] if ranked else 0.0
        ambiguous = len(ranked) > 1 and best - ranked[1][1] < margin
        speaker = ranked[0][0] if ranked and best >= min_ratio and not ambiguous else None
        active = [t for t in regular_turns if overlap(start, end, t["start"], t["end"]) > 0]
        simultaneous = any(
            x["speaker_id"] != y["speaker_id"] and
            overlap(start, end, max(x["start"], y["start"]), min(x["end"], y["end"])) > 0
            for i, x in enumerate(active) for y in active[i+1:]
        )
        assigned.append({**word, "speaker_id": speaker, "role": None,
                         "assignment_overlap_ratio": round(min(best, 1.0), 4),
                         "assignment_status": "assigned" if speaker else "ambiguous" if ambiguous else "unassigned",
                         "overlapping_speech": simultaneous})
    return assigned


def group_words(words, max_gap=1.0):
    segments = []
    for word in words:
        same = (segments and segments[-1]["speaker_id"] == word["speaker_id"]
                and segments[-1]["assignment_status"] == word["assignment_status"]
                and word["start"] - segments[-1]["end"] <= max_gap)
        if not same:
            segments.append({"speaker_id": word["speaker_id"], "role": None,
                             "assignment_status": word["assignment_status"],
                             "start": word["start"], "end": word["end"], "text": "", "words": [],
                             "overlapping_speech": False})
        segment = segments[-1]
        segment["end"] = max(segment["end"], word["end"])
        segment["words"].append(word)
        # Whisper conserva espacios iniciales: no insertar espacios antes de puntuación.
        segment["text"] = "".join(w["word"] for w in segment["words"]).strip()
        segment["overlapping_speech"] |= word["overlapping_speech"]
    return segments


def to_text(segments):
    return "\n".join(f"[{s['start']:.3f}–{s['end']:.3f}] "
                     f"{s['speaker_id'] or 'HABLANTE_DESCONOCIDO'}: {s['text']}" for s in segments)
