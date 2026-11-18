"""Whole-dialogue Gemini attribution. Speaker identity is never a prerequisite."""
import copy
import json
import time

from counter_presencial.config import digest
from counter_presencial.prompts import (OUTPUT_SCHEMA, PROMPT, PROMPT_VERSION, REASON_CODES,
                                       REDACTION_VERSION, SECOND_PASS)
from counter_presencial.shared import classification_sql, query

ROLES = frozenset(("ASESOR", "CLIENTE", "INCIERTO"))


class PromptBudgetExceeded(ValueError):
    pass


def safe_turns(turns, redact):
    """Deliberately project only text/ID/times: no speaker, catalog or raw text."""
    safe = []
    for t in turns:
        text = redact(t["text"])
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Redaction returned no usable text")
        safe.append({"turn_id": t["turn_id"], "text": text,
                     "start": t.get("start"), "end": t.get("end")})
    if len({t["turn_id"] for t in safe}) != len(safe):
        raise ValueError("Duplicate input turn IDs")
    return safe


def make_prompt(turns, targets, max_chars, hints=None):
    data = {"turns": turns, "target_turn_ids": targets,
            "reason_codes": sorted(REASON_CODES)}
    if hints is not None:
        data["acoustic_hints"] = hints
    prompt = PROMPT + (SECOND_PASS if hints is not None else "") + "\nINPUT_JSON:\n" + json.dumps(
        data, ensure_ascii=False, allow_nan=False)
    if len(prompt) > max_chars:
        raise PromptBudgetExceeded("Whole dialogue exceeds prompt budget; no truncation or API call")
    return prompt


def validate_decisions(turns, targets, response):
    expected, seen = set(targets), set()
    ids = {t["turn_id"] for t in turns}
    rows = response.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("Missing Gemini decisions")
    output = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Malformed decision")
        item = copy.deepcopy(raw)
        identity = item.get("turn_id")
        if identity not in expected or identity in seen:
            raise ValueError("Invented, unexpected or duplicate target ID")
        seen.add(identity)
        if item.get("role") not in ROLES or item.get("reason_code") not in REASON_CODES:
            raise ValueError("Invalid role or reason code")
        if type(item.get("requires_review")) is not bool or type(item.get("mixed_roles")) is not bool:
            raise ValueError("Invalid review/mixed flag")
        evidence = item.get("evidence_turn_ids")
        if not isinstance(evidence, list) or any(not isinstance(i, str) for i in evidence):
            raise ValueError("Invalid evidence IDs")
        valid = (len(set(evidence)) == len(evidence) and len(evidence) <= 3
                 and set(evidence).issubset(ids))
        if item["role"] != "INCIERTO":
            valid = valid and bool(evidence) and identity in evidence
        accepted = (valid and item["role"] != "INCIERTO" and not item["requires_review"]
                    and not item["mixed_roles"] and item["reason_code"] not in
                    ("insufficient_context", "mixed_roles", "asr_quality", "contradiction"))
        # Keep the proposed decision, never publish invented evidence as accepted.
        item["validation_status"] = "valid" if valid else "invalid_evidence"
        item["accepted_role"] = item["role"] if accepted else "INCIERTO"
        item["requires_review"] = not accepted
        output.append(item)
    if seen != expected:
        raise ValueError("Missing target decisions; do not publish a partial classification")
    return output


class BigQueryGemini:
    """Use the dedicated remote model/connection. Runtime never creates resources."""
    def __init__(self, client, model, location):
        self.client, self.model, self.location = client, model, location
        self.sql = classification_sql(model, OUTPUT_SCHEMA)

    def __call__(self, prompt):
        for attempt in range(3):
            response = json.loads(next(iter(query(self.client, self.sql, self.location,
                                                  {"prompt": prompt})))["response"])
            if not response.get("status"):
                metadata = response.get("full_response") or response.get("full_response_json") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                return {"decisions": response.get("decisions"),
                        "usage": metadata.get("usageMetadata", {}), "attempts": attempt + 1,
                        "model_version": metadata.get("modelVersion")}
            # Row status can be an error even when the SQL job succeeded.
            status = str(response["status"]).upper()
            if attempt == 2 or not any(x in status for x in (
                    "RESOURCE_EXHAUSTED", "RESOURCE EXHAUSTED", "UNAVAILABLE", "429", "503")):
                raise RuntimeError("Gemini row failed; inspect the restricted BigQuery job")
            time.sleep(2 ** attempt)


def role_key(acoustic_key, safe, model, config):
    return digest({"acoustic_key": acoustic_key, "safe_input": safe, "model": model,
                   "prompt_version": PROMPT_VERSION, "prompt_sha": digest(PROMPT + SECOND_PASS),
                   "redaction_version": REDACTION_VERSION, "fusion_version": "presencial-fusion-v1",
                   "targets_per_call": config.targets_per_call,
                   "max_second_pass": config.max_second_pass})


def classify(turns, provider, redact, config, checkpoint=None):
    """Each request sees the full dialogue; batching only limits output targets."""
    safe = safe_turns(turns, redact)
    results, calls = {}, []
    attempts = 0

    def request(targets):
        nonlocal attempts
        prompt = make_prompt(safe, targets, config.max_prompt_chars)
        # Optional cache saves each validated response before the next request.
        cache_key = digest({"prompt": prompt, "model": config.role_model})
        response = checkpoint("get", cache_key, None) if checkpoint else None
        reused = response is not None
        if response is None:
            if attempts >= config.max_first_pass_calls:
                raise RuntimeError("First-pass call budget exhausted; resume from cached responses")
            attempts += 1
            response = provider(prompt)
        try:
            decisions = validate_decisions(safe, targets, response)
        except ValueError:
            calls.append({"targets": targets, "cached": reused, "status": "invalid_response",
                          "usage": response.get("usage", {}), "attempts": response.get("attempts", 1)})
            if len(targets) <= 1:
                raise
            middle = len(targets) // 2
            # Like SAXO: reduce questions, never trim the global dialogue.
            request(targets[:middle])
            request(targets[middle:])
            return
        if checkpoint and not reused:
            checkpoint("put", cache_key, response)
        results.update({r["turn_id"]: r for r in decisions})
        calls.append({"targets": targets, "cached": reused, "usage": response.get("usage", {}),
                      "attempts": response.get("attempts", 1), "model_version": response.get("model_version")})
    for offset in range(0, len(safe), config.targets_per_call):
        request([t["turn_id"] for t in safe[offset:offset + config.targets_per_call]])
    rows = []
    for original in turns:
        decision = results[original["turn_id"]]
        final = decision["accepted_role"]
        if original.get("source_text_review"):
            final = "INCIERTO"
        rows.append({**original, "speaker_id": None, "role_initial": decision["role"],
                     "role_final": final, "role_source": "gemini_context" if final != "INCIERTO"
                     else "unresolved", "role_requires_review": final == "INCIERTO",
                     "requires_review": final == "INCIERTO" or bool(original.get("source_quality_review")),
                     "initial_decision": decision, "evidence_turn_ids": decision["evidence_turn_ids"]})
    return {"turns": rows, "safe_turns": safe, "calls": calls, "prompt_version": PROMPT_VERSION,
            "attribution_method": "text_context_only", "context_strategy": "whole_dialogue",
            "speaker_identity_verified": False,
            "candidate_segmentation": "saxo_word_pause_punctuation_v1"}
