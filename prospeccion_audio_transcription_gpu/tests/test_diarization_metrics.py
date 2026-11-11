import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from worker.config import ModelConfig
from worker.models import diarize


class DiarizationMetricsTests(unittest.TestCase):
    def test_threads_and_stage_metrics_with_two_speakers(self):
        torch = MagicMock()
        torch.get_num_threads.return_value = 3
        data = MagicMock()
        sf = SimpleNamespace(read=MagicMock(return_value=(data, 16000)))
        annotation = [(SimpleNamespace(start=0, end=1), "SPEAKER_00"),
                      (SimpleNamespace(start=1, end=2), "SPEAKER_01")]
        pipeline = MagicMock(return_value=SimpleNamespace(
            speaker_diarization=annotation, exclusive_speaker_diarization=annotation))
        factory = MagicMock()
        factory.from_pretrained.return_value = pipeline
        with patch.dict("sys.modules", {"torch": torch, "soundfile": sf,
                                       "pyannote.audio": SimpleNamespace(Pipeline=factory)}):
            result = diarize("audio.wav", ModelConfig(diarization_cpu_threads=3))
        torch.set_num_threads.assert_called_once_with(3)
        self.assertEqual(result["cpu_threads"], 3)
        self.assertEqual(len(result["exclusive"]), 2)
        self.assertEqual(set(result["timings"]), {"diarization_model_load_seconds",
                         "diarization_input_seconds", "diarization_inference_seconds"})
        self.assertTrue(all(value >= 0 for value in result["timings"].values()))

    def test_invalid_thread_count(self):
        with self.assertRaises(ValueError):
            ModelConfig(diarization_cpu_threads=0)
