import copy
import json
import unittest
from unittest.mock import MagicMock, patch
from worker.roles import validate_roles, classification_sql, run_manifest, published_results


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.segments = [
            {"speaker_id": "SPEAKER_01", "start": 1.0, "end": 2.0, "text": "Te explico la matrícula."},
            {"speaker_id": "SPEAKER_00", "start": 3.0, "end": 4.0, "text": "Quiero estudiar."},
            {"speaker_id": None, "start": 5.0, "end": 5.5, "text": "Sí."}]
        self.roles = [
            {"speaker_id": "SPEAKER_01", "role": "Asesor", "requires_review": False,
             "evidence": [{"segment_id": 0}]},
            {"speaker_id": "SPEAKER_00", "role": "Cliente", "requires_review": False,
             "evidence": [{"segment_id": 1}]}]

    def test_roles_are_dynamic_not_number_based(self):
        validated = validate_roles(self.segments, copy.deepcopy(self.roles))
        self.assertEqual(validated[0]["evidence"][0]["quote"], self.segments[0]["text"])
        self.assertEqual(validated[0]["evidence"][0]["start"], 1.0)
        segments = copy.deepcopy(self.segments)
        roles = copy.deepcopy(self.roles)
        for item in segments + roles:
            if item.get("speaker_id"):
                item["speaker_id"] = {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00"}[item["speaker_id"]]
        self.assertEqual(validate_roles(segments, roles)[0]["role"], "Asesor")

    def test_invalid_cross_speaker_and_unknown_segment_references_rejected(self):
        for evidence in ({"segment_id": 99}, {"segment_id": 1}, {"segment_id": 2},
                         {"segment_id": -1}, {"segment_id": True}, {"segment_id": "0"}):
            roles = copy.deepcopy(self.roles)
            roles[0]["evidence"] = [evidence]
            with self.assertRaises(ValueError):
                validate_roles(self.segments, roles)

    def test_quotes_and_times_are_reconstructed_from_source(self):
        roles = copy.deepcopy(self.roles)
        roles[0]["evidence"] = [{"segment_id": 0, "quote": "texto inventado", "start": 999}]
        output = validate_roles(self.segments, roles)
        self.assertEqual(output[0]["evidence"][0], {
            "segment_id": 0, "start": 1.0, "end": 2.0, "quote": "Te explico la matrícula."})

    def test_missing_duplicate_and_unknown_speakers_rejected(self):
        for roles in (self.roles[:1], self.roles + self.roles[:1],
                      self.roles + [{"speaker_id": "invented"}]):
            with self.assertRaises(ValueError):
                validate_roles(self.segments, roles)

    def test_unknown_role_requires_review(self):
        roles = copy.deepcopy(self.roles)
        roles[0].update(role="Desconocido", evidence=[], requires_review=True)
        validate_roles(self.segments, roles)
        roles[0]["requires_review"] = False
        with self.assertRaises(ValueError):
            validate_roles(self.segments, roles)

    def test_connection_cannot_inject_sql(self):
        with self.assertRaises(ValueError):
            classification_sql("gemini-3.5-flash", "bad'); DROP TABLE x")

    def test_remote_model_uses_existing_model_and_parameterized_prompt(self):
        sql = classification_sql("prd-utpbi-data-operation.adf_speech_analytics.gemini-3-5-flash")
        self.assertIn("AI.GENERATE_TABLE(", sql)
        self.assertIn("MODEL `prd-utpbi-data-operation.adf_speech_analytics.gemini-3-5-flash`", sql)
        self.assertIn("SELECT @prompt AS prompt", sql)
        self.assertNotIn("connection_id", sql)
        with self.assertRaises(ValueError):
            classification_sql("project.dataset.model`); DROP TABLE x")
        with self.assertRaises(ValueError):
            classification_sql("project.dataset.model", "project.US.connection")

    @patch("worker.roles.save_roles")
    @patch("worker.roles.query")
    @patch("worker.roles.BigQuerySink")
    @patch("worker.roles.published_results")
    def test_repeated_batch_skips_successful_classification(self, published, sink, query, save):
        result = {"processing_key": "key", "diarization_status": "completed",
                  "source": {"uri": "gs://bucket/audio"}}
        published.return_value = [(result, "gs://out/result.json")]
        query.return_value = [{"exists": 1}]
        run_manifest(MagicMock(), MagicMock(), "gs://b/manifest", "gs://b/results",
                     "project.dataset.results", "project.dataset.roles", "US")
        sink.return_value.write.assert_called_once()
        query.assert_called_once()
        save.assert_not_called()

    @patch("worker.roles.save_roles")
    @patch("worker.roles.query")
    @patch("worker.roles.BigQuerySink")
    @patch("worker.roles.published_results")
    def test_invalid_model_output_is_saved_as_failure(self, published, sink, query, save):
        result = {"processing_key": "key", "diarization_status": "completed",
                  "transcription": self.segments, "source": {"uri": "gs://bucket/audio"}}
        published.return_value = [(result, "gs://out/result.json")]
        query.side_effect = [[], [{"response": json.dumps({"status": "", "roles": []})}]]
        with self.assertRaises(RuntimeError):
            run_manifest(MagicMock(), MagicMock(), "gs://b/manifest", "gs://b/results",
                         "project.dataset.results", "project.dataset.roles", "US")
        self.assertEqual(save.call_args.args[6], "failed")

    def test_manifest_result_mismatch_is_rejected(self):
        storage = MagicMock()
        storage.bucket.return_value.blob.return_value.download_as_text.side_effect = [
            json.dumps({"count": 1, "models": {}, "items": [{"recording_id": "id", "source": {}}]}),
            json.dumps({"result_path": "attempts/abc/result.json"}),
            json.dumps({"processing_key": "wrong"})]
        with self.assertRaises(ValueError):
            list(published_results(storage, "gs://bucket/manifest.json", "gs://bucket/results"))
