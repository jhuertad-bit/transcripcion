"""Offline contract/integration tests. No real models, GCP or provider calls."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from counter_presencial.acoustics import make_turns, native_words, Models
from counter_presencial.config import Config
from counter_presencial.conversations import build_conversations, validate_sample_offsets
from counter_presencial.fusion import acoustic_match, fuse, second_pass
from counter_presencial.jobs import (completed, mark_published, prepare, publish_stage,
                                   run_acoustic, run_batch, run_roles)
from counter_presencial.redaction import Redactor
from counter_presencial.roles import (BigQueryGemini, PromptBudgetExceeded, classify,
                                      make_prompt, role_key, safe_turns, validate_decisions)


def config(**kwargs):
    return Config(output_root="gs://results/counter/presencial-vnext", allowed_buckets=("input",), project="example-cpv", **kwargs)


def turn(identity="T00001", text="Te explico los requisitos.", start=0., end=2., **kwargs):
    return {"turn_id": identity, "text": text, "start": start, "end": end,
            "source_window_index": 0, "source_quality_review": False, **kwargs}


def decision(identity="T00001", role="ASESOR", **kwargs):
    return {"turn_id": identity, "role": role, "evidence_turn_ids": [identity],
            "reason_code": "institutional_action", "requires_review": False,
            "mixed_roles": False, **kwargs}


def provider(prompt):
    data = json.loads(prompt.split("INPUT_JSON:\n", 1)[1])
    return {"decisions": [decision(i) for i in data["target_turn_ids"]],
            "usage": {"promptTokenCount": 100}}


class MemoryStore:
    def __init__(self):
        self.docs = {}

    def read(self, key):
        return deepcopy(self.docs.get(key))

    def put(self, key, value):
        if key in self.docs and self.docs[key] != value:
            raise ValueError("Immutable conflict")
        self.docs[key] = deepcopy(value)
        return "gs://output/presencial-vnext/" + key

    def put_text(self, key, value):
        return self.put(key, value)

    def lease(self, key):
        from contextlib import nullcontext
        return nullcontext(lambda: None)


class ConversationTests(unittest.TestCase):
    def test_complete_originals_group_fragments_once(self):
        rows = [{"gcs_uri": f"gs://input/audio_s{i:02}.flac", "s3_uri": "s3://original/full"}
                for i in (1, 2)]
        partial = build_conversations(rows)[0]
        self.assertEqual(partial["status"], "needs_source_review")
        full = build_conversations(rows, {partial["conversation_id"]: "gs://input/full.webm"})[0]
        self.assertEqual(full["parts"], [{"uri": "gs://input/full.webm", "offset_seconds": 0.}])

    def test_single_unsegmented_source_is_ready(self):
        self.assertEqual(build_conversations([{"gcs_uri": "gs://input/full.wav"}])[0]["status"], "ready")

    def test_fragment_offset_and_completeness_are_required(self):
        rows = [{"gcs_uri": f"gs://input/full_s{i}.flac", "s3_uri": "s3://original/full",
                 "segment_start_seconds": offset, "recording_complete": True}
                for i, offset in ((1, 0.), (2, 1.))]
        result = build_conversations(rows)[0]
        self.assertEqual(result["status"], "ready")
        offsets = validate_sample_offsets(result["parts"], [16000, 8000])
        self.assertEqual(offsets[1]["offset_samples"], 16000)
        rows[1]["recording_complete"] = False
        self.assertEqual(build_conversations(rows)[0]["status"], "needs_source_review")

    def test_gap_overlap_or_missing_fragment_is_not_closed(self):
        for offset in (None, .5, 2.):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_sample_offsets([{"offset_seconds": 0}, {"offset_seconds": offset}], [16000, 16000])
        rows = [{"gcs_uri": "gs://input/audio_s2.flac", "recording_complete": True,
                 "segment_start_seconds": 0}]
        self.assertEqual(build_conversations(rows)[0]["status"], "needs_source_review")

    def test_conflicting_originals_are_rejected(self):
        rows = [{"gcs_uri": "gs://input/a", "original_recording_id": "x", "original_gcs_uri": f"gs://input/{i}"}
                for i in (1, 2)]
        with self.assertRaises(ValueError):
            build_conversations(rows)

    def test_config_requires_isolation_and_pinned_models(self):
        with self.assertRaises(ValueError):
            Config(output_root="gs://output/existing", allowed_buckets=("input",))
        with self.assertRaises(ValueError):
            config().require_pinned_models()
        config(canary_revision="a" * 40, nemotron_revision="b" * 40).require_pinned_models()


class TimestampTests(unittest.TestCase):
    def test_native_relative_times_become_absolute(self):
        words, status = native_words("Hola, cliente.", [
            {"word": "Hola,", "start": .1, "end": .4},
            {"word": "cliente.", "start": .5, "end": .8}], 10., 11.)
        self.assertEqual(status, "native_ctc_verified")
        self.assertEqual(words[0]["start"], 10.1)

    def test_bad_times_and_lexical_mismatch_never_get_fabricated(self):
        for a, b in ((0, 0), (-1, .2), (0, 4), (float('nan'), 1), (False, .2)):
            self.assertEqual(native_words("hola", [{"word": "hola", "start": a, "end": b}], 0, 1)[0], [])
        self.assertEqual(native_words("hola", [{"word": "adiós", "start": 0, "end": .5}], 0, 1)[0], [])

    def test_untimed_text_and_short_replies_are_preserved(self):
        chunks = [{"text": "Mi número es 999123456.", "words": [], "timestamp_status": "missing"},
                  {"text": "Sí. Ya.", "words": [
                      {"word": "Sí.", "start": 2., "end": 2.2},
                      {"word": "Ya.", "start": 2.3, "end": 2.5}], "timestamp_status": "native_ctc_verified"}]
        rows = make_turns(chunks)
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0]["start"])
        self.assertIn("999123456", rows[0]["text"])
        self.assertEqual(rows[1]["text"], "Sí.")

    def test_canary_words_are_not_glued_together(self):
        chunks = [{"text": "Hola cliente.", "words": [
            {"word": "Hola", "start": 0., "end": .2},
            {"word": "cliente.", "start": .3, "end": .5}], "timestamp_status": "native_ctc_verified"}]
        self.assertEqual(make_turns(chunks)[0]["text"], "Hola cliente.")


class GeminiTests(unittest.TestCase):
    def test_global_context_without_speakers_for_every_batch(self):
        inputs = [turn(f"T{i}", speaker_id="SPEAKER_99", catalog={"name": "PRIVATE"}) for i in range(5)]
        requests = []

        def remote(prompt):
            data = json.loads(prompt.split("INPUT_JSON:\n")[1])
            self.assertEqual(len(data["turns"]), 5)
            self.assertEqual(set(data["turns"][0]), {"turn_id", "text", "start", "end"})
            self.assertNotIn("SPEAKER_99", prompt)
            self.assertNotIn("PRIVATE", prompt)
            requests.append(prompt)
            return provider(prompt)

        result = classify(inputs, remote, lambda s: s, config(targets_per_call=2))
        self.assertEqual(len(requests), 3)
        self.assertEqual([t["role_final"] for t in result["turns"]], ["ASESOR"] * 5)
        self.assertTrue(all(t["speaker_id"] is None for t in result["turns"]))

    def test_unknown_speaker_can_have_a_contextual_role(self):
        result = classify([turn(speaker_id=None)], provider, lambda s: s, config())
        self.assertEqual(result["turns"][0]["role_final"], "ASESOR")
        self.assertIsNone(result["turns"][0]["speaker_id"])

    def test_incomplete_response_reduces_targets_not_context(self):
        calls = []

        def remote(prompt):
            data = json.loads(prompt.split("INPUT_JSON:\n")[1])
            self.assertEqual(len(data["turns"]), 4)
            calls.append(len(data["target_turn_ids"]))
            return {"decisions": []} if len(data["target_turn_ids"]) > 2 else provider(prompt)

        result = classify([turn(f"T{i}") for i in range(4)], remote, lambda s: s, config())
        self.assertEqual(calls, [4, 2, 2])
        self.assertEqual(len(result["turns"]), 4)

    def test_no_truncation_or_call_when_global_context_exceeds_budget(self):
        remote = Mock()
        with self.assertRaises(PromptBudgetExceeded):
            classify([turn(text="a" * 10000)], remote, lambda s: s, config(max_prompt_chars=500))
        remote.assert_not_called()

    def test_call_budget_limits_malformed_provider(self):
        remote = Mock(return_value={"decisions": []})
        with self.assertRaises(RuntimeError):
            classify([turn(f"T{i}") for i in range(4)], remote, lambda s: s, config(max_first_pass_calls=1))
        self.assertEqual(remote.call_count, 1)

    def test_fabricated_duplicate_or_missing_ids_fail(self):
        for rows in ([decision("invented")], [decision(), decision()], []):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_decisions([turn()], ["T00001"], {"decisions": rows})

    def test_false_evidence_demotes_only_affected_role(self):
        result = validate_decisions([turn()], ["T00001"], {"decisions": [
            decision(evidence_turn_ids=["fabricated"])]})
        self.assertEqual(result[0]["accepted_role"], "INCIERTO")
        self.assertEqual(result[0]["role"], "ASESOR")

    def test_mixed_and_asr_uncertainty_are_never_accepted(self):
        response = lambda _: {"decisions": [decision(mixed_roles=True)]}
        self.assertEqual(classify([turn()], response, lambda s: s, config())["turns"][0]["role_final"], "INCIERTO")
        result = classify([turn(source_text_review=True)], provider, lambda s: s, config())
        self.assertEqual(result["turns"][0]["role_initial"], "ASESOR")
        self.assertEqual(result["turns"][0]["role_final"], "INCIERTO")

    def test_missing_timestamps_do_not_prevent_textual_role_attribution(self):
        result = classify([turn(start=None, end=None, source_quality_review=True)], provider, lambda s: s, config())
        self.assertEqual(result["turns"][0]["role_final"], "ASESOR")
        self.assertFalse(result["turns"][0]["role_requires_review"])
        self.assertTrue(result["turns"][0]["requires_review"])

    def test_valid_response_cache_avoids_repeated_calls(self):
        docs = {}

        def cache(action, key, value):
            if action == "put":
                docs[key] = value
            return docs.get(key)

        remote = Mock(side_effect=provider)
        classify([turn()], remote, lambda s: s, config(), cache)
        result = classify([turn()], remote, lambda s: s, config(), cache)
        self.assertEqual(remote.call_count, 1)
        self.assertTrue(result["calls"][0]["cached"])

    def test_redaction_failure_stops_remote_call(self):
        remote = Mock()
        with self.assertRaises(ValueError):
            classify([turn()], remote, lambda s: "", config())
        remote.assert_not_called()

    def test_bigquery_schema_is_per_turn_and_not_per_speaker(self):
        adapter = BigQueryGemini(Mock(), "example-cpv.counter_presencial_vnext.gemini_roles_vnext", "US")
        self.assertIn("decisions ARRAY", adapter.sql)
        self.assertNotIn("speaker_id STRING", adapter.sql)

    def test_bigquery_row_errors_are_not_sql_successes(self):
        adapter = BigQueryGemini(Mock(), "example-cpv.counter_presencial_vnext.gemini_roles_vnext", "US")
        with patch("counter_presencial.roles.query", return_value=iter([
                {"response": json.dumps({"status": "PERMISSION_DENIED"})}])):
            with self.assertRaises(RuntimeError):
                adapter("safe prompt")

    def test_role_key_changes_when_prompt_input_or_review_budget_changes(self):
        safe = safe_turns([turn()], lambda s: s)
        key = role_key("acoustic", safe, "model", config())
        self.assertNotEqual(key, role_key("acoustic", safe, "model2", config()))
        self.assertNotEqual(key, role_key("acoustic", safe, "model", config(max_second_pass=0)))


class FusionTests(unittest.TestCase):
    def classified(self, uncertain=True):
        inputs = [turn(f"T{i}", start=i * 3., end=i * 3. + 2., source_window_index=i) for i in range(4)]

        def remote(prompt):
            result = provider(prompt)
            if uncertain:
                result["decisions"][-1] = decision("T3", "INCIERTO", requires_review=True,
                                                    reason_code="insufficient_context")
            return result

        return classify(inputs, remote, lambda s: s, config())

    def test_overlap_and_missing_intervals_do_not_force_speaker(self):
        timeline = [{"start": 0, "end": 2, "speaker_id": "A"},
                    {"start": 1, "end": 2, "speaker_id": "B"}]
        self.assertIsNone(acoustic_match(turn(), timeline)["speaker_id"])
        self.assertIsNone(acoustic_match(turn(), [])["speaker_id"])

    def test_duplicate_same_speaker_intervals_do_not_inflate_coverage(self):
        match = acoustic_match(turn(), [{"start": 0, "end": 2, "speaker_id": "A"}] * 3)
        self.assertEqual(match["coverage"], 1.)
        self.assertEqual(match["speaker_id"], "A")

    def test_independent_consensus_resolves_unknown_and_preserves_original(self):
        original = self.classified()
        result = fuse(original, [{"start": 0, "end": 12, "speaker_id": "A"}])
        self.assertEqual(result["turns"][-1]["role_final"], "ASESOR")
        self.assertEqual(result["turns"][-1]["role_initial"], "INCIERTO")
        self.assertEqual(original["turns"][-1]["role_final"], "INCIERTO")
        self.assertNotIn("T3", result["speaker_profiles"]["A"]["anchor_ids"])

    def test_no_circular_anchors_and_no_automatic_role_alternation(self):
        result = fuse(self.classified(), [])
        self.assertEqual(result["turns"][-1]["role_final"], "INCIERTO")
        self.assertEqual([r["role_final"] for r in result["turns"][:3]], ["ASESOR"] * 3)

    def test_leave_one_out_quarantines_contradiction(self):
        original = self.classified(False)
        original["turns"][-1].update(role_initial="CLIENTE", role_final="CLIENTE")
        original["turns"][-1]["initial_decision"].update(role="CLIENTE", accepted_role="CLIENTE")
        result = fuse(original, [{"start": 0, "end": 12, "speaker_id": "A"}])
        self.assertEqual(result["turns"][-1]["role_source"], "conflict_quarantine")
        self.assertEqual(result["turns"][-1]["role_initial"], "CLIENTE")

    def test_second_pass_uses_safe_text_and_excludes_target_from_examples(self):
        result = self.classified(False)
        for safe in result["safe_turns"]:
            safe["text"] = "[PERSON_1] informa requisitos."
        result["turns"][-1].update(role_initial="CLIENTE", role_final="CLIENTE")
        result = fuse(result, [{"start": 0, "end": 12, "speaker_id": "A"}])
        calls = []

        def remote(prompt):
            data = json.loads(prompt.split("INPUT_JSON:\n")[1])
            self.assertIn("acoustic_hints", data)
            self.assertEqual(len(data["turns"]), 4)
            self.assertNotIn("T3", [x["turn_id"] for x in data["acoustic_hints"]["examples"]])
            self.assertTrue(all("[PERSON_1]" in t["text"] for t in data["turns"]))
            calls.append(prompt)
            return provider(prompt)

        reviewed = second_pass(result, remote, config(max_second_pass=1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(reviewed["turns"][-1]["role_final"], "ASESOR")
        self.assertEqual(reviewed["turns"][-1]["role_initial"], "CLIENTE")


class RedactionTests(unittest.TestCase):
    def test_engine_is_reused_without_linking_identity_maps(self):
        original = Redactor(engine=SimpleNamespace(analyze=lambda **kwargs: []))
        original("999123456")
        other = original.new_conversation()
        self.assertIs(other.engine, original.engine)
        self.assertEqual(other.labels, {})

    def test_local_peru_redaction_preserves_contact_intent(self):
        redact = Redactor.__new__(Redactor)
        redact.labels = {}
        redact.engine = SimpleNamespace(analyze=lambda **kwargs: [])
        safe = redact("Puedes llamarme al 999 123 456, mi DNI es 12345678 y mi correo es yo@example.com.")
        self.assertIn("Puedes llamarme", safe)
        for value in ("999", "12345678", "yo@example.com"):
            self.assertNotIn(value, safe)
        self.assertIn("[PHONE_NUMBER_1]", safe)
        self.assertEqual(redact("999 123 456"), "[PHONE_NUMBER_1]")


class StageTests(unittest.TestCase):
    def test_prepare_skips_published_before_applying_limit(self):
        import sys
        bq = SimpleNamespace(QueryJobConfig=lambda **kw: kw,
                             ScalarQueryParameter=lambda *args: args)
        client = Mock()
        client.query.return_value.result.return_value = [
            {"gcs_uri": f"gs://input/{name}.wav"} for name in ("a", "b", "c")]
        cfg = config(canary_revision="a" * 40, nemotron_revision="b" * 40, max_conversations=2)
        store = MemoryStore()
        store.source = lambda uri, *args: {"uri": uri, "generation": "1", "size_bytes": 100}
        env = {"BQ_SOURCE_TABLE": "example-cpv.counter_presencial_vnext.catalog_vnext", "PROCESS_DAY_FROM": "2026-11-02",
               "PROCESS_DAY_TO": "2026-11-02", "BATCH_ID": "first"}
        modules = {"google.cloud": SimpleNamespace(bigquery=bq), "google.cloud.bigquery": bq}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
            first = prepare(cfg, store, None, client)
            self.assertEqual(first["count"], 2)
            for item in first["items"]:
                mark_published(item, {"role_key": item["conversation_id"]}, cfg, store)
            os.environ["BATCH_ID"] = "second"
            second = prepare(cfg, store, None, client)
        self.assertEqual(second["count"], 1)
        self.assertNotIn(second["items"][0]["conversation_id"],
                         {i["conversation_id"] for i in first["items"]})

    def test_model_load_fails_closed_without_cuda(self):
        import sys
        with patch.dict(sys.modules, {"torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))}):
            with self.assertRaises(RuntimeError):
                Models(config()).load()

    def test_completed_integrity_and_resume_skip_inference(self):
        store = MemoryStore()
        item = {"conversation_id": "c", "acoustic_key": "key"}
        publish_stage(store, "acoustic", "key", {"transcription": []}, "attempt", lambda: None)
        with patch("counter_presencial.jobs.process_audio") as infer:
            self.assertEqual(run_acoustic(item, config(), store, None), {"transcription": []})
            infer.assert_not_called()
        store.docs["acoustic/key/attempts/attempt/result.json"]["transcription"] = ["changed"]
        with self.assertRaises(ValueError):
            completed(store, "acoustic", "key")

    def test_lost_lease_never_publishes_completed(self):
        store = MemoryStore()
        with self.assertRaises(RuntimeError):
            publish_stage(store, "acoustic", "key", {}, "attempt", Mock(side_effect=RuntimeError("lost")))
        self.assertIsNone(store.read("acoustic/key/completed.json"))

    def test_role_only_retry_does_not_repeat_gemini(self):
        store = MemoryStore()
        acoustic = {"transcription": [turn()], "diarization": [], "source_parts": [],
                    "duration_seconds": 2., "quality_status": "unvalidated"}
        item = {"conversation_id": "c", "acoustic_key": "key"}
        remote = Mock(side_effect=provider)
        first = run_roles(item, acoustic, config(), store, remote, lambda s: s)
        second = run_roles(item, acoustic, config(), store, remote, lambda s: s)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["turns"][0]["role_final"], "ASESOR")

    def test_batch_retains_successes_and_continues_after_failure(self):
        store, visited = MemoryStore(), []
        items = [{"conversation_id": i, "acoustic_key": i} for i in ("a", "b", "c")]

        def process(item):
            visited.append(item["conversation_id"])
            if item["conversation_id"] == "b":
                raise ValueError("private transcript should not be logged")

        with self.assertLogs(level="ERROR") as logs:
            failures = run_batch(items, "acoustic", process, store)
        self.assertEqual(visited, ["a", "b", "c"])
        self.assertEqual(failures, ["b"])
        self.assertNotIn("private transcript", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
