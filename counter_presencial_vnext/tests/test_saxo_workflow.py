"""Failure-path and compatibility tests; no cloud, model downloads or GPU."""
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from counter_presencial.acoustics import Models, process_audio
from counter_presencial.canary_process import CanaryProcess
from counter_presencial.canary_worker import safe_stamps, serve
from counter_presencial.main import resolve_stage
from counter_presencial.saxo_canary_quality import remove_repetition_loop, speech_windows
from counter_presencial.saxo_canary_nemo_timestamps import normalize_native_words
from counter_presencial.saxo_nemotron_four import extract_four_speaker_timeline
from test_presencial_vnext import config
from test_presencial_vnext import MemoryStore
from counter_presencial.jobs import run_acoustic, run_roles
from test_isolation import renderer, settings


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_paul_job_role_contract_and_explicit_conflicts(self):
        for name, expected in (("prepare", "prepare"), ("worker", "acoustic"), ("roles", "roles")):
            self.assertEqual(resolve_stage(environment={"JOB_ROLE": name}), expected)
        self.assertEqual(resolve_stage("acoustic", {}), "acoustic")
        for arg, env in (("prepare", {"JOB_ROLE": "worker"}), (None, {"JOB_ROLE": "invalid"})):
            with self.assertRaises(ValueError):
                resolve_stage(arg, env)

    def test_single_job_and_runtime_identity_only_have_our_resources(self):
        files = renderer.render(settings())
        inventory = json.loads(files["inventory.json"])
        self.assertEqual(inventory["jobs"], {"pipeline": "counter-presencial-vnext-gpu"})
        self.assertEqual(inventory["job_roles"], ["prepare", "worker", "roles"])
        self.assertEqual(files["create_jobs.sh"].count("jobs create "), 1)
        self.assertNotIn("--args=", files["create_jobs.sh"])
        wf_env = json.loads(files["workflow.env.yaml"])
        self.assertEqual(wf_env["JOB_NAME"], "counter-presencial-vnext-gpu")
        self.assertTrue(wf_env["MANIFEST_GCS_ROOT"].endswith("/presencial-vnext/manifests"))
        self.assertNotIn("cpv-roles@", files["bootstrap.sh"])
        self.assertNotIn("cpv-prepare@", files["bootstrap.sh"])

    def test_manual_runner_stops_before_roles_when_worker_fails(self):
        files = renderer.render(settings())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "generated").mkdir()
            (root / "generated/run_manual.sh").write_text(files["run_manual.sh"])
            (root / "gcloud").write_text(
                '#!/usr/bin/env python3\nimport json,os,sys\n'
                'with open(os.environ["CPV_TEST_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                'if sys.argv[1:3]==["storage","cat"]: print(\'{"task_count":1}\')\n'
                'if any("JOB_ROLE=worker," in x for x in sys.argv): sys.exit(7)\n')
            (root / "gcloud").chmod(0o700)
            import os
            env = {**os.environ, "PATH": str(root) + ":" + os.environ["PATH"], "CPV_TEST_LOG": str(root / "calls")}
            result = subprocess.run(["bash", str(root / "generated/run_manual.sh"), "retry"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            self.assertNotIn("JOB_ROLE=roles", (root / "calls").read_text())


class SaxoPoliciesTests(unittest.TestCase):
    def test_significant_fifth_channel_cannot_be_silently_discarded(self):
        rows = [{"Start": i * 2., "End": i * 2. + 1.5, "Speaker": i} for i in range(5)]
        with self.assertRaisesRegex(ValueError, "Refusing to discard"):
            extract_four_speaker_timeline(rows, audio_seconds=12)
        rows[-1]["End"] = rows[-1]["Start"] + .1
        timeline, diagnostics = extract_four_speaker_timeline(rows, audio_seconds=12)
        self.assertEqual(len({t["speaker"] for t in timeline}), 4)
        self.assertIn("SPEAKER_04", diagnostics["nemotron_discarded_insignificant_channels"])

    def test_overlap_is_transcribed_once_and_silence_is_excluded(self):
        windows = speech_windows([{"start": 10., "end": 13.}, {"start": 11., "end": 15.}], 60.)
        self.assertEqual(len(windows), 1)
        self.assertLess(sum(w["end"] - w["start"] for w in windows), 6.)
        self.assertEqual(speech_windows([], 60), [])

    def test_repetition_cleanup_preserves_spoken_prefix_and_following_speech(self):
        source = "Te enviaré los requisitos. " + "no " * 20 + "Mi hija termina el colegio este año."
        text, audit = remove_repetition_loop(source)
        self.assertTrue(audit["loop_detected"])
        self.assertIn("Te enviaré los requisitos.", text)
        self.assertIn("Mi hija termina el colegio este año.", text)
        words, reason = normalize_native_words([{ "word": "no", "start": 0., "end": 1.}],
            cleaned_text=text, original_text=source, window_start=10., window_end=34.)
        self.assertEqual(words, [])
        self.assertEqual(reason, "text_changed_by_repetition_cleanup")


class CanaryProcessTests(unittest.TestCase):
    def test_model_process_is_reused_and_is_terminated_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "stub.py"
            script.write_text('import json,sys,os\nfor line in sys.stdin:\n'
                ' r=json.loads(line)\n'
                ' x={"runtime":{"pid":os.getpid()}} if r["op"]=="load" else {"result":{"text":"Sí.","native_stamps":[]}}\n'
                ' print(json.dumps({"ok":True,**x}),flush=True)\n')
            client = CanaryProcess(config(canary_revision="a"*40, nemotron_revision="b"*40),
                                    python=sys.executable, script=script, timeout=3)
            try:
                first = client.transcribe(Path(folder) / "audio.wav")
                process = client.process
                second = client.transcribe(Path(folder) / "audio.wav")
                self.assertEqual(first["runtime"], second["runtime"])
                self.assertIs(client.process, process)
            finally:
                client.close()
            self.assertIsNotNone(process.poll())

    def test_malformed_or_stalled_worker_is_stopped(self):
        for response in ('print("not-json",flush=True)', 'import time; time.sleep(10)'):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                script = Path(folder) / "broken.py"
                script.write_text('import sys\nsys.stdin.readline()\n' + response + '\n')
                client = CanaryProcess(config(canary_revision="a"*40, nemotron_revision="b"*40),
                                        python=sys.executable, script=script, timeout=.1)
                with self.assertRaises((ValueError, TimeoutError)):
                    client.load()
                self.assertIsNone(client.process)
                self.assertIsNone(client.log)

    def test_library_stdout_cannot_corrupt_protocol_or_leak_exception_text(self):
        class Worker:
            def handle(self, request):
                print("library progress")
                if request["op"] == "fail":
                    raise ValueError("private transcript")
                return {"result": {"text": "Sí."}}
        source, sink = io.StringIO('{"op":"ok"}\n{"op":"fail"}\n'), io.StringIO()
        with patch("sys.stderr", io.StringIO()):
            serve(Worker(), source, sink)
        rows = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(rows[0]["result"]["text"], "Sí.")
        self.assertEqual(rows[1], {"ok": False, "error_type": "ValueError"})
        self.assertNotIn("private transcript", sink.getvalue())

    def test_missing_interpreter_closes_its_temporary_log(self):
        client = CanaryProcess(config(canary_revision="a"*40, nemotron_revision="b"*40),
                                python="/nonexistent/canary/python")
        with self.assertRaises(FileNotFoundError):
            client.load()
        self.assertIsNone(client.process)
        self.assertIsNone(client.log)

    def test_invalid_native_times_remain_unaligned_and_json_safe(self):
        stamps = safe_stamps([{"word": "Hola", "start": float("nan"), "end": .5}])
        json.dumps(stamps, allow_nan=False)
        words, reason = normalize_native_words(stamps, cleaned_text="Hola", original_text="Hola",
                                               window_start=0., window_end=1.)
        self.assertEqual(words, [])
        self.assertEqual(reason, "invalid_native_word_timestamp")

    def test_nemotron_is_released_before_loading_canary(self):
        events = []
        class Child:
            runtime = {}
            def __init__(self, cfg): pass
            def load(self): events.append("canary_load")
            def close(self): events.append("canary_close")
        models = Models(config())
        models.nemotron = object()
        torch = SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: events.append("nemotron_release")))
        with patch.dict(sys.modules, {"torch": torch}), patch("counter_presencial.canary_process.CanaryProcess", Child):
            models.load_canary()
            self.assertLess(events.index("nemotron_release"), events.index("canary_load"))
            models.close()
        self.assertIn("canary_close", events)


class WindowResumeTests(unittest.TestCase):
    def test_corrupt_checkpoint_is_rejected_before_reusing_its_text(self):
        store = MemoryStore()
        store.docs["acoustic/key/checkpoints/diarization.json"] = {"value": {}, "sha256": "wrong"}
        def infer(item, config, store, models, folder, checkpoint):
            return checkpoint("get", "diarization", None)
        with patch("counter_presencial.jobs.process_audio", side_effect=infer), self.assertRaisesRegex(
                ValueError, "checkpoint integrity"):
            run_acoustic({"acoustic_key": "key"}, config(), store, None)

    def test_skipped_audio_never_calls_gemini(self):
        provider = Mock()
        acoustic = {"transcription": [], "diarization": [], "source_parts": [],
                    "duration_seconds": 10., "quality_status": "unvalidated", "skip_reason": "no_significant_speech"}
        result = run_roles({"acoustic_key": "key", "conversation_id": "silent"}, acoustic,
                           config(), MemoryStore(), provider, lambda text: text)
        provider.assert_not_called()
        self.assertEqual(result["execution_status"], "skipped")
        self.assertEqual(result["serialized_level_1"], "")

    def run_audio(self, *, empty=False, fail=False):
        from contextlib import ExitStack
        class Signal:
            ndim = 1
            def __init__(self, size): self.size = size
            def __len__(self): return self.size
            def __getitem__(self, span): return Signal(span.stop - span.start)
        class ModelsStub:
            canary_runtime = {}
            diarization_diagnostics = {"nemotron_significant_speaker_count": 1}
            def __init__(self): self.diar_calls = self.asr_calls = 0
            def diarize(self, signal):
                self.diar_calls += 1
                return [] if empty else [{"start": .2, "end": 49., "speaker_id": "SPEAKER_00"}]
            def unload_diarizer(self): pass
            def transcribe(self, path):
                self.asr_calls += 1
                if fail and self.asr_calls == 2: raise RuntimeError("transient failure")
                return {"text": "Hola.", "native_stamps": [{"word": "Hola.", "start": .1, "end": .5}]}
        models, docs = ModelsStub(), {}
        def checkpoint(action, key, value):
            if action == "put": docs[key] = deepcopy(value)
            return deepcopy(docs.get(key))
        store = SimpleNamespace(download=lambda part, path: path.write_bytes(b"input"))
        torch = SimpleNamespace(version=SimpleNamespace(cuda="12.8"), cuda=SimpleNamespace(
            reset_peak_memory_stats=lambda: None, synchronize=lambda: None,
            max_memory_allocated=lambda: 0, get_device_name=lambda i: "test"))
        modules = {"torch": torch, "numpy": SimpleNamespace(concatenate=lambda values: Signal(sum(map(len, values)))),
                   "soundfile": SimpleNamespace(read=lambda *a, **k: (Signal(800000), 16000), write=lambda *a, **k: None)}
        item = {"conversation_id": "conversation", "parts": [{"size_bytes": 10, "offset_seconds": 0}]}
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, modules), patch(
                "counter_presencial.audio.normalize", side_effect=lambda src, wav, **kw: wav.write_bytes(b"wav")):
            if fail:
                with self.assertRaises(RuntimeError):
                    process_audio(item, config(), store, models, Path(folder), checkpoint)
            result = process_audio(item, config(), store, models, Path(folder), checkpoint)
        return result, models, docs

    def test_retry_reuses_diarization_and_successful_windows(self):
        result, models, docs = self.run_audio(fail=True)
        self.assertEqual(models.diar_calls, 1)
        self.assertEqual(models.asr_calls, 4)  # success+failure, then only the 2 pending windows
        self.assertEqual(len(result["chunks"]), 3)
        self.assertEqual(result["word_alignment"], "canary_nemo_ctc_native")
        self.assertEqual(set(docs), {"diarization", "window-000000", "window-000001", "window-000002"})

    def test_no_speech_skips_canary_without_inventing_text(self):
        result, models, _ = self.run_audio(empty=True)
        self.assertEqual(models.asr_calls, 0)
        self.assertEqual(result["transcription"], [])
        self.assertEqual(result["skip_reason"], "no_significant_speech")


if __name__ == "__main__":
    unittest.main()
