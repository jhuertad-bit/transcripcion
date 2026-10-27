import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from worker.catalog import metadata, read_item, table_id, prepare
from worker.config import ModelConfig
from test_counter import MemoryRepository


class CatalogTests(unittest.TestCase):
    def row(self, suffix):
        return {"gcs_uri": f"gs://counter-bucket/audio_{suffix}.flac",
                "file_name": f"audio_{suffix}.flac", "s3_uri": "s3://original/audio.webm",
                "campus_code": "015", "process_day": "2026-10-24"}

    def test_segments_share_original_but_keep_order(self):
        a, b = metadata(self.row("s01")), metadata(self.row("s02"))
        self.assertEqual(a["original_recording_id"], b["original_recording_id"])
        self.assertEqual((a["segment_index"], b["segment_index"]), (1, 2))
        self.assertEqual(a["campus_code"], "015")
        self.assertEqual(a["speaker_scope"], "segment")

    def test_complete_audio_has_no_invented_segment(self):
        self.assertIsNone(metadata(self.row("full"))["segment_index"])

    def test_reject_table_injection(self):
        with self.assertRaises(ValueError):
            table_id("project.dataset.table`; DELETE")

    @patch.dict(os.environ, {"MANIFEST_URI": "gs://output/manifests/a.json",
                            "MANIFEST_GCS_ROOT": "gs://output/manifests",
                            "CLOUD_RUN_TASK_INDEX": "1", "CLOUD_RUN_TASK_COUNT": "2"})
    def test_manifest_selects_one_segment_and_checks_config(self):
        client = MagicMock()
        payload = {"count": 2, "models": ModelConfig.from_env().as_dict(), "items": [self.row("s01"), self.row("s02")]}
        blob = client.bucket.return_value.blob.return_value
        blob.download_as_text.return_value = json.dumps(payload)
        self.assertEqual(read_item(client)["file_name"], "audio_s02.flac")
        payload["models"]["language"] = "en"
        blob.download_as_text.return_value = json.dumps(payload)
        with self.assertRaises(ValueError):
            read_item(client)

    @patch.dict(os.environ, {"BQ_SOURCE_TABLE": "project.dataset.catalog", "BQ_SOURCE_LOCATION": "US",
                            "ALLOWED_INPUT_BUCKETS": "counter-bucket", "OUTPUT_GCS_ROOT": "gs://output/results",
                            "MANIFEST_GCS_ROOT": "gs://output/manifests", "BATCH_ID": "batch-1",
                            "BATCH_MAX_FILES": "1", "PROCESS_DAY_FROM": "", "PROCESS_DAY_TO": ""})
    def test_prepare_skips_completed_before_applying_limit(self):
        # A completed old file must not starve later arrivals when max_files=1.
        bq = MagicMock()
        bq.query.return_value.result.return_value = [self.row("s01"), self.row("s02")]
        first, second = MemoryRepository(), MemoryRepository()
        first.read_json = lambda *a: {"result_uri": "done"}
        storage = MagicMock()
        api = SimpleNamespace(QueryJobConfig=lambda **kw: kw, ScalarQueryParameter=lambda *a: a)
        with patch.dict("sys.modules", {"google": SimpleNamespace(cloud=SimpleNamespace(bigquery=api)), "google.cloud": SimpleNamespace(bigquery=api), "google.cloud.bigquery": api}), \
             patch("worker.catalog.GCSRepository", side_effect=[first, second]):
            prepare(storage, bq)
        uploads = storage.bucket.return_value.blob.return_value.upload_from_string.call_args_list
        manifest = json.loads(uploads[0].args[0])
        self.assertEqual(manifest["items"][0]["catalog"]["file_name"], "audio_s02.flac")
        self.assertNotIn("process_day >=", bq.query.call_args.args[0])
        self.assertEqual(json.loads(uploads[1].args[0])["count"], 1)


if __name__ == "__main__":
    unittest.main()
