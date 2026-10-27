import copy
import json
import os
import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import patch
from counter_common.contract import AudioRequest, parse_gcs_uri
from cf_trigger.handler import build_run_body, handle
from worker.alignment import assign_words, group_words, to_text
from worker.config import ModelConfig
from worker.pipeline import run, processing_key


class RequestTests(unittest.TestCase):
    def test_valid_unicode_object(self):
        self.assertEqual(parse_gcs_uri("gs://counter-bucket/carpeta/atención.m4a"),
                         ("counter-bucket", "carpeta/atención.m4a"))

    def test_invalid_requests(self):
        for body in [None, [], {}, {"audio_uri": "https://example.com/a"},
                     {"audio_uri": "gs://counter-bucket/"},
                     {"audio_uri": "gs://other-bucket/a"},
                     {"audio_uri": "gs://counter-bucket/a", "force": "false"},
                     {"audio_uri": "gs://counter-bucket/a", "recording_id": "../a"},
                     {"audio_uri": "gs://counter-bucket/a", "job": "other-job"}]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                AudioRequest.from_dict(body, ("counter-bucket",))

    def test_default_id_and_overrides(self):
        request = AudioRequest.from_dict({"audio_uri": "gs://counter-bucket/a"})
        self.assertEqual(len(request.recording_id), 24)
        body = build_run_body(request)
        self.assertEqual(body["overrides"]["taskCount"], 1)
        env = body["overrides"]["containerOverrides"][0]["env"]
        self.assertEqual({e["name"]: e["value"] for e in env}, request.job_environment())

    @patch.dict(os.environ, {"ALLOWED_INPUT_BUCKETS": "counter-bucket"})
    def test_http_returns_operation_not_execution(self):
        class FakeRequest:
            method = "POST"
            def get_json(self, **kwargs):
                return {"audio_uri": "gs://counter-bucket/a"}
        content, status, _ = handle(FakeRequest(), submit=lambda req: "projects/p/locations/r/operations/123")
        self.assertEqual(status, 202)
        self.assertIn("operation_name", json.loads(content))
        self.assertNotIn("execution_id", json.loads(content))

    def test_wrong_method(self):
        class FakeRequest:
            method = "GET"
        self.assertEqual(handle(FakeRequest())[1], 405)


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.a = {"speaker_id": "A", "start": 0.0, "end": 1.0}
        self.b = {"speaker_id": "B", "start": 1.0, "end": 2.0}

    def word(self, start, end, text=" hola"):
        return {"start": start, "end": end, "word": text}

    def test_speaker_change_and_text(self):
        words = assign_words([self.word(0, .3), self.word(.3, .5, ","), self.word(1.2, 1.5, " buenos días")],
                             [self.a, self.b], [self.a, self.b])
        groups = group_words(words)
        self.assertEqual([g["speaker_id"] for g in groups], ["A", "B"])
        self.assertEqual(groups[0]["text"], "hola,")
        self.assertIn("A: hola,", to_text(groups))

    def test_tie_is_ambiguous(self):
        word = assign_words([self.word(.9, 1.1)], [self.a, self.b], [self.a, self.b])[0]
        self.assertIsNone(word["speaker_id"])
        self.assertEqual(word["assignment_status"], "ambiguous")

    def test_missing_voice_not_assigned_to_nearest(self):
        word = assign_words([self.word(4, 5)], [self.a], [self.a])[0]
        self.assertIsNone(word["speaker_id"])
        self.assertEqual(word["assignment_status"], "unassigned")

    def test_little_intersection_unassigned(self):
        self.assertIsNone(assign_words([self.word(.99, 1.5)], [self.a], [self.a])[0]["speaker_id"])

    def test_real_overlap_vs_sequential_speakers(self):
        w = self.word(.8, 1.2)
        self.assertFalse(assign_words([w], [self.a, self.b], [self.a, self.b])[0]["overlapping_speech"])
        overlap_b = {**self.b, "start": .7}
        self.assertTrue(assign_words([w], [self.a, self.b], [self.a, overlap_b])[0]["overlapping_speech"])

    def test_invalid_intervals(self):
        for a, b in [(1, 1), (2, 1), (-1, 1), (0, float("nan"))]:
            with self.subTest(start=a, end=b), self.assertRaises(ValueError):
                assign_words([self.word(a, b)], [], [])

    def test_pause_creates_new_intervention(self):
        assigned = assign_words([self.word(.1, .2), self.word(3, 4)],
                                [{**self.a, "end": 5}], [{**self.a, "end": 5}])
        self.assertEqual(len(group_words(assigned)), 2)


class MemoryRepository:
    source = {"uri": "gs://counter-bucket/a", "generation": "7", "size_bytes": 100}
    def __init__(self):
        self.data, self.texts, self.downloads = {}, {}, 0

    @contextmanager
    def lock(self, key):
        yield

    def read_json(self, key, suffix):
        return copy.deepcopy(self.data.get((key, suffix)))

    def write_json(self, key, suffix, value, immutable=False):
        if immutable and (key, suffix) in self.data:
            raise RuntimeError("Immutable collision")
        self.data[key, suffix] = copy.deepcopy(value)
        return f"gs://output/{key}/{suffix}"

    def write_text(self, key, suffix, text):
        self.texts[key, suffix] = text
        return f"gs://output/{key}/{suffix}"

    def download(self, path):
        self.downloads += 1


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.request = AudioRequest("gs://counter-bucket/a", "counter-1")
        self.config = ModelConfig()
        self.key = processing_key(self.request, self.repo.source, self.config)

    def execute(self, **overrides):
        dependencies = {"normalize_audio": lambda *a, **kw: 2.0,
                        "transcribe_audio": lambda *a: {"words": [{"word": " Hola", "start": 0, "end": 1}],
                                                       "timestamp_gaps": [], "language": "es", "language_probability": 1},
                        "diarize_audio": lambda *a: {"regular": [{"speaker_id": "A", "start": 0, "end": 2}],
                                                     "exclusive": [{"speaker_id": "A", "start": 0, "end": 2}]}}
        dependencies.update(overrides)
        # Toda la E/S está simulada: estas pruebas no necesitan crear/borrar temporales.
        with patch("worker.pipeline.tempfile.TemporaryDirectory", return_value=nullcontext("unused-test-path")):
            return run(self.repo, self.request, self.config, **dependencies)

    def test_persist_and_skip(self):
        self.assertEqual(self.execute()["status"], "completed")
        self.assertEqual(self.execute()["status"], "already_completed")
        self.assertEqual(self.repo.downloads, 1)

    def test_segment_catalog_is_preserved_in_result(self):
        catalog = {"original_recording_id": "original", "segment_index": 2, "speaker_scope": "segment"}
        self.execute(catalog_metadata=catalog)
        completed = self.repo.read_json(self.key, "completed.json")
        result = self.repo.read_json(self.key, completed["result_path"])
        self.assertEqual(result["catalog"], catalog)

    def test_transcription_only_never_calls_diarization(self):
        self.config = ModelConfig(enable_diarization=False)
        def forbidden(*args):
            raise AssertionError("No se debe invocar Pyannote")
        outcome = self.execute(diarize_audio=forbidden)
        key = outcome["processing_key"]
        completed = self.repo.read_json(key, "completed.json")
        result = self.repo.read_json(key, completed["result_path"])
        self.assertEqual(result["diarization_status"], "disabled")
        self.assertIsNone(result["diarization"])
        self.assertIsNone(result["transcription"][0]["speaker_id"])
        self.assertIsNone(result["transcription"][0]["overlapping_speech"])
        self.assertIn("Hola", result["serialized_level_1"])
        self.assertNotIn("HABLANTE", result["serialized_level_1"])
        self.assertNotEqual(key, self.key)

    @patch.dict(os.environ, {"ENABLE_DIARIZATION": "false"})
    def test_diarization_switch_from_environment(self):
        self.assertFalse(ModelConfig.from_env().enable_diarization)

    def test_failure_preserves_previous_result(self):
        self.execute()
        previous = self.repo.read_json(self.key, "completed.json")
        self.request = AudioRequest(self.request.audio_uri, self.request.recording_id, force=True)
        def fail(*args):
            raise RuntimeError("Model failed")
        with self.assertRaises(RuntimeError):
            self.execute(transcribe_audio=fail)
        self.assertEqual(previous, self.repo.read_json(self.key, "completed.json"))
        self.assertEqual(self.repo.read_json(self.key, "status.json")["status"], "failed")

    def test_bigquery_failure_does_not_publish_success(self):
        class FailedSink:
            def write(self, *args):
                raise RuntimeError("BQ unavailable")
        with self.assertRaises(RuntimeError):
            self.execute(sink=FailedSink())
        self.assertIsNone(self.repo.read_json(self.key, "completed.json"))
        self.assertEqual(self.repo.read_json(self.key, "status.json")["status"], "failed")

    def test_key_changes_with_generation_and_config(self):
        self.assertNotEqual(self.key, processing_key(self.request, {**self.repo.source, "generation": "8"}, self.config))
        self.assertNotEqual(self.key, processing_key(self.request, self.repo.source, ModelConfig(max_speakers=4)))

    def test_repeated_request_can_sync_bigquery_without_inference(self):
        self.execute()
        class Sink:
            calls = 0
            def write(self, *args):
                self.calls += 1
        sink = Sink()
        self.execute(sink=sink)
        self.assertEqual(sink.calls, 1)
        self.assertEqual(self.repo.downloads, 1)


if __name__ == "__main__":
    unittest.main()
