"""Conversation identities and source assembly. Never infer missing offsets."""
from collections import defaultdict
import hashlib
import math
import re

from counter_presencial.shared import parse_gcs_uri
def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def build_conversations(rows, originals=None):
    """Original mapping: original_recording_id -> GCS URI of the complete audio.

    Fragmented inputs need explicit source offsets AND recording_complete=true;
    otherwise they remain needs_source_review and are not sent to the GPU.
    """
    originals = originals or {}
    groups = defaultdict(list)
    for source_row in rows:
        row = dict(source_row)
        uri = row.get("gcs_uri")
        if not uri:
            continue
        parse_gcs_uri(uri)
        original = row.get("s3_uri") or uri
        identity = str(row.get("original_recording_id") or hashlib.sha256(original.encode()).hexdigest()[:24])
        groups[identity].append(row)
    output = []
    for identity, entries in sorted(groups.items()):
        complete_uris = {r["original_gcs_uri"] for r in entries if r.get("original_gcs_uri")}
        if identity in originals:
            complete_uris.add(originals[identity])
        if len(complete_uris) > 1:
            raise ValueError("Conflicting complete originals for conversation " + identity)
        reason = None
        if complete_uris:
            parts = [{"uri": next(iter(complete_uris)), "offset_seconds": 0.0}]
        else:
            unique = {}
            for r in entries:
                uri = r["gcs_uri"]
                index = r.get("segment_index")
                if index is None:
                    match = re.search(r"_s(\d+)\.[^.]+$", r.get("file_name") or uri, re.I)
                    index = int(match.group(1)) if match else None
                part = {"uri": uri, "segment_index": index,
                        "offset_seconds": r.get("segment_start_seconds")}
                if uri in unique and unique[uri] != part:
                    raise ValueError("Conflicting metadata for source object")
                unique[uri] = part
            parts = list(unique.values())
            if len(parts) == 1 and parts[0]["segment_index"] is None:
                parts[0]["offset_seconds"] = 0.0
            else:
                indices = [p["segment_index"] for p in parts]
                valid_indices = (all(type(i) is int for i in indices)
                                 and sorted(indices) == list(range(1, len(parts) + 1)))
                offsets_valid = all(finite(p["offset_seconds"]) and p["offset_seconds"] >= 0
                                    for p in parts)
                if not valid_indices or not offsets_valid or not all(
                        r.get("recording_complete") is True for r in entries):
                    reason = "fragment_continuity_not_verified"
                parts.sort(key=lambda p: (p["segment_index"] or 0, p["uri"]))
        for part in parts:
            parse_gcs_uri(part["uri"])
        output.append({"conversation_id": identity, "parts": parts,
                       "source_files": sorted({r["gcs_uri"] for r in entries}),
                       "status": "ready" if reason is None else "needs_source_review",
                       "review_reason": reason})
    return output


def validate_sample_offsets(parts, lengths, rate=16000):
    """Only contiguous source timelines are supported; never close real gaps."""
    if len(parts) != len(lengths) or not parts:
        raise ValueError("Invalid decoded part count")
    position = 0
    offsets = []
    for part, length in zip(parts, lengths):
        offset = part.get("offset_seconds")
        if not finite(offset) or abs(round(offset * rate) - position) > 1:
            raise ValueError("Missing, overlapping or discontinuous fragment timeline")
        if type(length) is not int or length <= 0:
            raise ValueError("Empty decoded source part")
        offsets.append({**part, "offset_samples": position, "samples": length})
        position += length
    return offsets
