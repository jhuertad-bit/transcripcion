"""Bounded JSONL IPC to Canary's separate NeMo environment, within the same Job."""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile


class CanaryProcess:
    def __init__(self, config, *, python=None, script=None, timeout=900):
        self.config = config
        self.python = python or os.environ.get("CANARY_PYTHON", "/opt/canary/bin/python")
        self.script = str(script or Path(__file__).with_name("canary_worker.py"))
        self.timeout = timeout
        self.process = None
        self.log = None
        self.runtime = {}

    def _request(self, message):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Canary worker is not running")
        try:
            self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            self.process.stdin.flush()
            if not select.select([self.process.stdout], [], [], self.timeout)[0]:
                raise TimeoutError("Canary worker response timed out")
            line = self.process.stdout.readline()
            if not line or len(line) > 4_000_000:
                raise RuntimeError("Invalid Canary worker response size")
            response = json.loads(line)
            if not isinstance(response, dict) or response.get("ok") is not True:
                kind = response.get("error_type", "worker_failed") if isinstance(response, dict) else "invalid_response"
                # Exception content may contain transcripts or tokens; expose only its type.
                raise RuntimeError("Canary worker failed: " + str(kind))
            return response
        except (OSError, ValueError, RuntimeError, TimeoutError):
            self.close()
            raise

    def load(self):
        if self.process is not None:
            return
        self.config.require_pinned_models()
        self.log = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                [self.python, "-u", self.script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log, text=True, bufsize=1)
            self.runtime = self._request({"op": "load", "model": self.config.canary_model,
                                          "revision": self.config.canary_revision})["runtime"]
        except BaseException:
            self.close()
            raise

    def transcribe(self, path):
        self.load()
        response = self._request({"op": "transcribe", "path": str(Path(path).resolve())})
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            self.close()
            raise RuntimeError("Malformed Canary transcription")
        result["runtime"] = dict(self.runtime)
        return result

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        if self.log is not None:
            self.log.close()
            self.log = None
