"""Real import/API checks for two isolated GPU runtimes; no weights/cloud/GPU."""
import inspect
from importlib.metadata import version
import json
import sys


def check(role):
    if sys.version_info < (3, 12):
        raise RuntimeError("This deployment profile requires Python 3.12+")
    expected = {"torch": "2.8.0", "torchaudio": "2.8.0"}
    if role == "pipeline":
        expected.update({"transformers": "5.18.0.dev0", "huggingface-hub": "1.33.0", "tokenizers": "0.23.2"})
    elif role == "canary":
        expected.update({"nemo-toolkit": "2.7.3", "transformers": "4.57.6"})
    else:
        raise ValueError("Unknown runtime role")
    actual = {name: version(name) for name in expected}
    for name, wanted in expected.items():
        if actual[name].split("+")[0] != wanted:
            raise RuntimeError(f"Unexpected {name}: {actual[name]} (expected {wanted})")
    import torch
    import torchaudio
    if torch.version.cuda != "12.8":
        raise RuntimeError("Expected the CUDA 12.8 Torch build")
    if role == "canary":
        from nemo.collections.asr.models import ASRModel, EncDecMultiTaskModel
        if not callable(ASRModel.restore_from) or "timestamps" not in inspect.signature(EncDecMultiTaskModel.transcribe).parameters:
            raise RuntimeError("Canary native timestamp APIs are unavailable")
    else:
        from transformers import (AutoModelForAudioFrameClassification, Nemotron3DiarizationConfig,
                                  Nemotron3DiarizationForAudioFrameClassification,
                                  Nemotron3DiarizationProcessor, NemotronAsrStreamingFeatureExtractor)
        if AutoModelForAudioFrameClassification._model_mapping[Nemotron3DiarizationConfig] is not Nemotron3DiarizationForAudioFrameClassification:
            raise RuntimeError("Nemotron auto mapping is unavailable")
        p = Nemotron3DiarizationProcessor(feature_extractor=NemotronAsrStreamingFeatureExtractor())
        segments = p.extract_speaker_dict(torch.tensor([[[10., -10.], [10., -10.], [-10., 10.]]]),
                                          torch.tensor([[1, 1, 0]]))
        if len(segments[0]) != 1 or segments[0][0]["Speaker"] != 0:
            raise RuntimeError("Nemotron extraction/masking contract failed")
        import es_core_news_md
        from google.cloud import bigquery, storage
        from presidio_analyzer import AnalyzerEngine
        if es_core_news_md.load().lang != "es":
            raise RuntimeError("Spanish redaction model is unavailable")
    print(json.dumps({"runtime_smoke": "passed", "role": role, "python": sys.version.split()[0],
                      "packages": actual, "cuda_build": torch.version.cuda, "model_inference": "not tested"}))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_gpu_runtime.py pipeline|canary")
    check(sys.argv[1])
