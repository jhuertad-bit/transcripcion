"""Prevent accidental access to legacy resources and validate standalone delivery."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from counter_presencial.config import Config
from counter_presencial.isolation import Resources

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_deployment", ROOT / "scripts/render_deployment.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def settings():
    return {"project_id": "example-cpv", "new_project_confirmed": True,
            "region": "us-central1", "canary_revision": "a" * 40,
            "nemotron_revision": "b" * 40, "gemini_endpoint": "gemini-example",
            "process_day_from": "2026-11-02", "process_day_to": "2026-11-02",
            "new_github_repository": "example/counter-presencial-independent", "github_branch": "main"}


class IsolationTests(unittest.TestCase):
    def test_legacy_project_and_unconfirmed_project_fail_before_rendering(self):
        for changes in ({"project_id": "prd-utpbi-data-operation"}, {"new_project_confirmed": False},
                        {"new_github_repository": "nsilvera-zat/counter-audio-transcription"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                renderer.render({**settings(), **changes})

    def test_runtime_refuses_cross_project_and_cross_bucket_configuration(self):
        env = json.loads(renderer.render(settings())["runtime.env.yaml"])
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.from_env().project, "example-cpv")
        for key, value in {
            "GCP_PROJECT": "prd-utpbi-data-operation", "GOOGLE_CLOUD_PROJECT": "other-project",
            "BQ_SOURCE_TABLE": "other-project.old.catalog", "ALLOWED_INPUT_BUCKETS": "old-input",
            "PRESENCIAL_OUTPUT_GCS_ROOT": "gs://old-output/presencial-vnext",
            "PRESENCIAL_ROLE_MODEL": "other-project.old.gemini",
            "PRESENCIAL_BQ_RESULTS_TABLE": "other-project.old.results_vnext",
            "PRESENCIAL_ORIGINALS_MAP_URI": "gs://old-input/map.json",
        }.items():
            with self.subTest(key=key), patch.dict(os.environ, {**env, key: value}, clear=True):
                with self.assertRaises(ValueError):
                    Config.from_env()
        without_model = deepcopy(env)
        del without_model["PRESENCIAL_ROLE_MODEL"]
        with patch.dict(os.environ, without_model, clear=True), self.assertRaises(ValueError):
            Config.from_env()

    def test_rendered_commands_are_new_scoped_and_parse_as_shell(self):
        files = renderer.render(settings())
        for name, contents in files.items():
            self.assertNotIn("prd-utpbi-data-operation", contents)
            self.assertNotIn("serializa-audio-repo", contents)
            self.assertNotIn("CREATE OR REPLACE", contents)
            if not name.endswith(".sh"):
                continue
            with self.subTest(name=name):
                check = subprocess.run(["bash", "-n"], input=contents, text=True, capture_output=True)
                self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("jobs create counter-presencial-vnext-gpu", files["create_jobs.sh"])
        self.assertNotIn("jobs update", files["create_jobs.sh"])
        self.assertNotIn("execute", files["build.sh"])
        self.assertNotIn("deploy", files["build.sh"])

    def test_renderer_rejects_command_and_sql_injection(self):
        for key, value in {"project_id": "a; false", "gemini_endpoint": "gemini-x'); DROP TABLE x;--",
                           "new_github_repository": "owner/$(false)", "github_branch": "main;false",
                           "canary_revision": "main"}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                renderer.render({**settings(), key: value})

    def test_cloud_shell_runner_shards_and_resumes_without_repreparing(self):
        files = renderer.render(settings())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "generated").mkdir()
            script = root / "generated/run_manual.sh"
            script.write_text(files["run_manual.sh"])
            binary = root / "gcloud"
            binary.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                              'with open(os.environ["CPV_TEST_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                              'if sys.argv[1:3] == ["storage","cat"]: print(\'{"task_count":2}\')\n')
            binary.chmod(0o700)
            log = root / "calls.jsonl"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "CPV_TEST_LOG": str(log)}
            for args, expected_count in (([], 3), (["resume-batch"], 2)):
                log.write_text("")
                subprocess.run(["bash", str(script), *args], env=env, check=True, capture_output=True)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                executions = [x for x in calls if x[:3] == ["run", "jobs", "execute"]]
                self.assertEqual(len(executions), expected_count)
                gpu = next(x for x in executions if x[3] == "counter-presencial-vnext-gpu")
                self.assertIn("--tasks=2", gpu)
                self.assertTrue(all("--project=example-cpv" in x for x in calls))

    def test_no_imports_of_the_legacy_project(self):
        import ast
        for path in (ROOT / "counter_presencial").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(module.startswith(("worker", "counter_common")), str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.startswith(("worker", "counter_common")), str(path))


if __name__ == "__main__":
    unittest.main()
