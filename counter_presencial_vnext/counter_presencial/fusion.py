"""Acoustic/contextual fusion. Frozen original evidence prevents circular voting."""
from collections import defaultdict
from copy import deepcopy

from counter_presencial.config import digest
from counter_presencial.conversations import finite
from counter_presencial.roles import make_prompt, validate_decisions

VERSION = "presencial-fusion-v1"
DIRECT_REASONS = {"institutional_action", "personal_need", "contact_followup", "availability_response"}


def acoustic_match(turn, timeline):
    start, end = turn.get("start"), turn.get("end")
    empty = {"speaker_id": None, "coverage": 0., "purity": 0., "overlap": 0.}
    if not finite(start) or not finite(end) or end <= start:
        return empty
    relevant, boundaries = [], [start, end]
    for t in timeline:
        a, b = max(start, t["start"]), min(end, t["end"])
        if b > a:
            relevant.append((a, b, t["speaker_id"]))
            boundaries.extend((a, b))
    activity, covered, overlap = defaultdict(float), 0., 0.
    edges = sorted(set(boundaries))
    for a, b in zip(edges, edges[1:]):
        speakers = {s for lo, hi, s in relevant if lo < b and hi > a}
        if speakers:
            covered += b - a
        if len(speakers) > 1:
            overlap += b - a
        elif speakers:
            activity[next(iter(speakers))] += b - a
    speaker = max(activity, key=activity.get) if activity else None
    purity = activity[speaker] / covered if speaker and covered else 0.
    coverage, simultaneous = covered / (end - start), overlap / (end - start)
    clean = coverage >= .85 and purity >= .95 and simultaneous <= .01
    return {"speaker_id": speaker if clean else None, "coverage": coverage,
            "purity": purity, "overlap": simultaneous}


def fuse(classified, timeline):
    result = deepcopy(classified)
    pools = defaultdict(list)
    for row in result["turns"]:
        row["acoustic_match"] = acoustic_match(row, timeline)
        row["speaker_id"] = row["acoustic_match"]["speaker_id"]
        initial = row["initial_decision"]
        if (row["speaker_id"] and row["role_final"] in ("ASESOR", "CLIENTE")
                and initial["reason_code"] in DIRECT_REASONS
                and not row.get("source_quality_review") and len(row["text"].split()) >= 3):
            pools[row["speaker_id"]].append(row)
    # Profiles use original first-pass evidence only, never inferred/second-pass rows.
    result["speaker_profiles"] = {}
    for speaker, anchors in pools.items():
        roles = {a["role_final"] for a in anchors}
        windows = {a.get("source_window_index") for a in anchors}
        seconds = sum(min(6., a["end"] - a["start"]) for a in anchors)
        ready = len(anchors) >= 3 and len(windows) >= 2 and seconds >= 5 and len(roles) == 1
        result["speaker_profiles"][speaker] = {
            "role": next(iter(roles)) if ready else None,
            "anchor_ids": [a["turn_id"] for a in anchors], "ready": ready}
    for row in result["turns"]:
        if not row["speaker_id"] or row.get("source_quality_review") or row["initial_decision"]["mixed_roles"]:
            continue
        independent = [a for a in pools[row["speaker_id"]] if a["turn_id"] != row["turn_id"]]
        roles = {a["role_initial"] for a in independent}
        windows = {a.get("source_window_index") for a in independent}
        seconds = sum(min(6., a["end"] - a["start"]) for a in independent)
        expected = next(iter(roles)) if len(roles) == 1 and len(windows) >= 2 and seconds >= 5 else None
        nearby = [a for a in independent if abs(a["start"] - row["start"]) <= 90]
        if not expected or len(independent) < 3 or len(nearby) < 2:
            continue
        row["role_expected"] = expected
        row["independent_anchor_ids"] = [a["turn_id"] for a in independent]
        if row["role_final"] == "INCIERTO":
            row.update(role_final=expected, role_source="acoustic_context_consensus",
                       evidence_turn_ids=[a["turn_id"] for a in nearby[:3]], requires_review=False,
                       role_requires_review=False)
        elif row["role_final"] != expected:
            row.update(role_final="INCIERTO", role_source="conflict_quarantine", requires_review=True,
                       role_requires_review=True)
    result["fusion_version"] = VERSION
    return result


def second_pass(result, provider, config, checkpoint=None):
    result = deepcopy(result)
    by_id = {t["turn_id"]: t for t in result["safe_turns"]}
    reviewed = 0
    for row in result["turns"]:
        if (row["role_final"] != "INCIERTO" or row.get("source_quality_review")
                or row["initial_decision"]["mixed_roles"] or not row.get("speaker_id")):
            continue
        if reviewed >= config.max_second_pass:
            break
        profile = result["speaker_profiles"].get(row["speaker_id"], {})
        examples = [i for i in row.get("independent_anchor_ids", profile.get("anchor_ids", []))
                    if i != row["turn_id"]][:6]
        hints = {"target_speaker": row["speaker_id"], "identity_verified": False,
                 "examples": [by_id[i] for i in examples],
                 "nearby_speakers": [{"turn_id": t["turn_id"], "speaker_id": t["speaker_id"]}
                                     for t in result["turns"] if finite(t.get("start"))
                                     and abs(t["start"] - row["start"]) <= 90]}
        prompt = make_prompt(result["safe_turns"], [row["turn_id"]], config.max_prompt_chars, hints)
        key = digest({"prompt": prompt, "model": config.role_model})
        answer = checkpoint("get", key, None) if checkpoint else None
        reused = answer is not None
        if answer is None:
            answer = provider(prompt)
        decision = validate_decisions(result["safe_turns"], [row["turn_id"]], answer)[0]
        if checkpoint and not reused:
            checkpoint("put", key, answer)
        row["second_pass_decision"] = decision
        reviewed += 1
        choice = decision["accepted_role"]
        expected = row.get("role_expected") or profile.get("role")
        if choice != "INCIERTO" and (not expected or choice == expected):
            row.update(role_final=choice, role_source="gemini_second_pass",
                       evidence_turn_ids=decision["evidence_turn_ids"], requires_review=False,
                       role_requires_review=False)
        result["calls"].append({"targets": [row["turn_id"]], "pass": 2, "cached": reused,
                                "usage": answer.get("usage", {}), "attempts": answer.get("attempts", 1),
                                "model_version": answer.get("model_version")})
    result["second_pass_count"] = reviewed
    return result
