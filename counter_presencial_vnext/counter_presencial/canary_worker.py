"""Canary-only process. stdout is JSONL; NeMo logs never contaminate its protocol."""
import contextlib
from importlib.metadata import version
import json
import math
from numbers import Real
from pathlib import Path
import re
import sys


def safe_stamps(stamps):
    """JSON-safe scalars. Invalid times stay invalid; text is never dropped."""
    if not isinstance(stamps, list):
        return None
    result = []
    for row in stamps:
        if not isinstance(row, dict):
            return None
        item = {"word": row.get("word") if isinstance(row.get("word"), str) else None}
        for key in ("start", "end"):
            value = row.get(key)
            item[key] = float(value) if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) else None
        result.append(item)
    return result


class CanaryWorker:
    def __init__(self):
        self.model = None

    def handle(self, request):
        import torch
        if request.get("op") == "load":
            if self.model is not None:
                raise ValueError("Model already loaded")
            if request.get("model") != "nvidia/canary-1b-v2" or not re.fullmatch(
                    r"[a-f0-9]{40}", request.get("revision", "")):
                raise ValueError("Unsupported or unpinned Canary checkpoint")
            if not torch.cuda.is_available():
                raise RuntimeError("Canary requires CUDA")
            from huggingface_hub import snapshot_download
            from nemo.collections.asr.models import ASRModel
            folder = snapshot_download(request["model"], revision=request["revision"],
                                       allow_patterns=["*.nemo"])
            files = list(Path(folder).glob("*.nemo"))
            if len(files) != 1:
                raise RuntimeError("Expected exactly one pinned Canary checkpoint")
            self.model = ASRModel.restore_from(str(files[0])).to(device="cuda", dtype=torch.float32).eval()
            if getattr(self.model, "timestamps_asr_model", None) is None:
                raise RuntimeError("Canary checkpoint lacks native CTC timestamps")
            return {"runtime": {name: version(name) for name in
                                ("nemo-toolkit", "transformers", "torch", "torchaudio", "huggingface-hub")}}
        if request.get("op") != "transcribe" or self.model is None:
            raise ValueError("Load Canary before transcription")
        path = Path(request["path"])
        if not path.is_absolute() or not path.is_file() or path.suffix != ".wav":
            raise ValueError("Expected an existing absolute WAV path")
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            output = self.model.transcribe([str(path)], source_lang="es", target_lang="es", timestamps=True)
        torch.cuda.synchronize()
        if not output or not hasattr(output[0], "timestamp"):
            raise RuntimeError("NeMo did not return timestamped hypotheses")
        stamps = output[0].timestamp
        return {"result": {"text": str(output[0].text or ""),
                           "native_stamps": safe_stamps(stamps.get("word")) if isinstance(stamps, dict) else None,
                           "peak_vram_bytes": torch.cuda.max_memory_allocated()}}


def serve(worker, source, sink):
    for line in source:
        try:
            if len(line) > 16384:
                raise ValueError("Oversized Canary request")
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = worker.handle(request)
            response = {"ok": True, **result}
        except Exception as exc:
            response = {"ok": False, "error_type": type(exc).__name__}
        sink.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
        sink.flush()
        if not response["ok"]:
            return


if __name__ == "__main__":
    serve(CanaryWorker(), sys.stdin, sys.stdout)
