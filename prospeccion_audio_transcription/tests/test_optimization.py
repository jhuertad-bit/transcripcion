import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from worker.catalog import read_items
from worker.config import ModelConfig
from worker.main import process_batch
from worker.models import transcribe, whisper_model


class OptimizationTests(unittest.TestCase):
    def tearDown(self):
        whisper_model.cache_clear()

    @patch.dict(os.environ, {"MANIFEST_URI": "gs://output/manifests/batch.json",
                            "MANIFEST_GCS_ROOT": "gs://output/manifests",
                            "CLOUD_RUN_TASK_COUNT": "3"})
    def test_every_file_belongs_to_exactly_one_task(self):
        client = MagicMock()
        payload = {"count": 7, "task_count": 3, "files_per_worker": 3,
                   "models": ModelConfig.from_env().as_dict(), "items": list(range(7))}
        client.bucket.return_value.blob.return_value.download_as_text.return_value = json.dumps(payload)
        groups = []
        for index in range(3):
            with patch.dict(os.environ, {"CLOUD_RUN_TASK_INDEX": str(index)}):
                groups.append(read_items(client))
        self.assertEqual(groups, [[0, 1, 2], [3, 4, 5], [6]])
        with patch.dict(os.environ, {"CLOUD_RUN_TASK_INDEX": "3"}):
            with self.assertRaises(ValueError):
                read_items(client)

    def test_batch_preserves_successes_after_individual_failure(self):
        seen = []
        def process(item):
            seen.append(item)
            if item == 2:
                raise RuntimeError("simulated")
            return {"status": "completed"}
        with patch("worker.main.logging.exception"), patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                process_batch([1, 2, 3], process)
        self.assertEqual(seen, [1, 2, 3])

    @patch.dict(os.environ, {"BUNDLED_WHISPER_MODEL": "test-model", "WHISPER_LOCAL_PATH": "/opt/test"})
    def test_whisper_loaded_once_from_bundled_path_for_two_files(self):
        model = MagicMock()
        word = SimpleNamespace(word=" Hola", start=0.1, end=0.4, probability=0.9)
        model.transcribe.side_effect = lambda *a, **kw: (
            iter([SimpleNamespace(text="Hola", words=[word])]),
            SimpleNamespace(language="es", language_probability=1))
        constructor = MagicMock(return_value=model)
        config = ModelConfig(whisper_model="test-model")
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}), \
             patch("worker.models.os.path.isfile", return_value=True):
            first = transcribe("one.wav", config)
            second = transcribe("two.wav", config)
        constructor.assert_called_once_with("/opt/test", device="cpu", compute_type="int8",
                                            cpu_threads=4, local_files_only=True)
        self.assertFalse(first["model_reused"])
        self.assertTrue(second["model_reused"])
        self.assertEqual(first["words"], second["words"])
        self.assertEqual(model.transcribe.call_count, 2)

    def test_model_configuration_change_does_not_reuse_wrong_model(self):
        constructor = MagicMock()
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}), \
             patch("worker.models.os.path.isfile", return_value=False):
            whisper_model("first", "cpu", "int8", 4)
            whisper_model("second", "cpu", "int8", 4)
        self.assertEqual(constructor.call_count, 2)
