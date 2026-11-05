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

    def test_review_mode_demotes_only_role_with_wrong_speaker_evidence(self):
        roles = copy.deepcopy(self.roles)
        roles[0]["evidence"] = [{"segment_id": 1}]
        output = validate_roles(self.segments, roles, allow_review=True)
        self.assertEqual(output[0]["role"], "Desconocido")
        self.assertTrue(output[0]["requires_review"])
        self.assertEqual(output[0]["evidence"], [])
        self.assertEqual(output[0]["proposed_role"], "Asesor")
        self.assertEqual(output[0]["proposed_evidence"], [{"segment_id": 1}])
        self.assertIn("otro speaker", output[0]["validation_issues"][0])
        self.assertEqual(output[1]["role"], "Cliente")
        self.assertEqual(output[1]["evidence"][0]["quote"], "Quiero estudiar.")

    def test_review_mode_still_rejects_missing_or_fabricated_speakers(self):
        for roles in (self.roles[:1], self.roles + [{"speaker_id": "invented"}]):
            with self.assertRaises(ValueError):
                validate_roles(self.segments, copy.deepcopy(roles), allow_review=True)

    def test_role_without_evidence_is_reviewable_not_accepted(self):
        roles = copy.deepcopy(self.roles)
        roles[0]["evidence"] = []
        output = validate_roles(self.segments, roles, allow_review=True)
        self.assertEqual(output[0]["role"], "Desconocido")
        self.assertTrue(output[0]["requires_review"])

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

    def test_unknown_flag_is_normalized_for_publication(self):
        roles = copy.deepcopy(self.roles)
        roles[0].update(role="Desconocido", evidence=[], requires_review=False, review_reason="")
        output = validate_roles(self.segments, roles, allow_review=True)
        self.assertTrue(output[0]["requires_review"])
        self.assertTrue(output[0]["review_reason"])
        self.assertEqual(output[0]["role"], "Desconocido")
        self.assertEqual(output[1]["role"], "Cliente")

    @patch("worker.roles.save_roles")
    @patch("worker.roles.query")
    @patch("worker.roles.BigQuerySink")
    @patch("worker.roles.published_results")
    def test_without_speakers_records_file_review_without_calling_gemini(self, published, sink, query, save):
        for segments in ([], [self.segments[2]]):
            with self.subTest(segments=segments):
                query.reset_mock()
                query.return_value = []
                save.reset_mock()
                result = {"processing_key": "key", "diarization_status": "completed",
                          "recording_id": "recording", "transcription": segments,
                          "source": {"uri": "gs://bucket/audio"}}
                published.return_value = [(result, "gs://out/result.json")]
                run_manifest(MagicMock(), MagicMock(), "gs://b/manifest", "gs://b/results",
                             "project.dataset.results", "project.dataset.roles", "US")
                query.assert_called_once()  # Solo consulta la clasificación previa.
                self.assertEqual(save.call_args.args[6], "completed")
                marker = save.call_args.args[7][0]
                self.assertIsNone(marker["speaker_id"])
                self.assertEqual(marker["scope"], "recording")
                self.assertEqual(marker["role"], "Desconocido")
                self.assertTrue(marker["requires_review"])
                self.assertEqual(marker["evidence"], [])

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
