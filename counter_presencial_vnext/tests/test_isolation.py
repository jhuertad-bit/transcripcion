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
    return {"project_id": "example-cpv", "isolated_resources_confirmed": True,
            "region": "us-central1", "canary_revision": "a" * 40,
            "nemotron_revision": "b" * 40, "gemini_endpoint": "gemini-example",
            "process_day_from": "2026-11-02", "process_day_to": "2026-11-02",
            "github_repository": "example/counter-presencial-independent", "github_branch": "main"}


class IsolationTests(unittest.TestCase):
    def test_invalid_project_and_unconfirmed_namespace_fail_before_rendering(self):
        for changes in ({"project_id": "invalid project"}, {"isolated_resources_confirmed": False},
                        {"source_directory": "../counter_audio_transcription"}):
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

    def test_prd_build_and_runtime_allow_only_our_resource_namespace(self):
        cfg = {**settings(), "project_id": "prd-utpbi-data-operation",
               "github_repository": "jhuertad-bit/transcripcion",
               "source_directory": "counter_presencial_vnext"}
        files = renderer.render(cfg)
        env = json.loads(files["runtime.env.yaml"])
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Config.from_env().project, cfg["project_id"])
        self.assertIn("_SOURCE_DIR=counter_presencial_vnext", files["create_trigger.sh"])
        self.assertIn("--build-config=counter_presencial_vnext/cloudbuild.yaml", files["create_trigger.sh"])
        self.assertIn("counter_presencial_vnext/**", files["create_trigger.sh"])
        for key, old_value in {
            "BQ_SOURCE_TABLE": "prd-utpbi-data-operation.raw_queue_smart.queuesmart_mp3_catalog",
            "PRESENCIAL_ROLE_MODEL": "prd-utpbi-data-operation.adf_speech_analytics.gemini-3-5-flash",
            "ALLOWED_INPUT_BUCKETS": "prd-utp-stg-queuesmart",
            "PRESENCIAL_OUTPUT_GCS_ROOT": "gs://prd-utp-stg-queuesmart/counter/presencial-vnext",
        }.items():
            with self.subTest(key=key), patch.dict(os.environ, {**env, key: old_value}, clear=True):
                with self.assertRaises(ValueError):
                    Config.from_env()
        # BigQuery data permissions must never be granted at the PRD project level.
        self.assertNotIn("--role=roles/bigquery.dataEditor", files["bootstrap.sh"])
        self.assertNotIn("--role=roles/bigquery.dataViewer", files["bootstrap.sh"])
        self.assertNotIn("--role=roles/bigquery.connectionUser", files["bootstrap.sh"])
        self.assertNotIn("--role=roles/run.viewer", files["bootstrap.sh"])
        self.assertIn("ON SCHEMA `prd-utpbi-data-operation.counter_presencial_vnext`", files["dataset_access.sql"])

    def test_connection_grant_preserves_policy_and_uses_only_our_connection(self):
        import io
        spec = importlib.util.spec_from_file_location("grant_connection", ROOT / "scripts/grant_connection.py")
        grant_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grant_module)
        previous = {"etag": "original-etag", "version": 3, "bindings": [
            {"role": "roles/bigquery.connectionAdmin", "members": ["user:operator@example.com"]},
            {"role": "roles/bigquery.connectionUser", "members": ["user:existing@example.com"]}]}
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            return io.BytesIO(json.dumps(previous if len(calls) == 1 else {}).encode())

        grant_module.grant("prd-utpbi-data-operation", token="test-token", opener=opener)
        self.assertEqual(len(calls), 2)
        expected = "projects/prd-utpbi-data-operation/locations/us/connections/counter-presencial-vnext-gemini:"
        self.assertTrue(all(expected in url for url, _ in calls))
        policy = calls[1][1]["policy"]
        self.assertEqual(policy["etag"], "original-etag")
        self.assertEqual(policy["bindings"][0], previous["bindings"][0])
        self.assertEqual(policy["bindings"][1]["members"], ["user:existing@example.com",
            "serviceAccount:cpv-pipeline@prd-utpbi-data-operation.iam.gserviceaccount.com"])

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
                           "github_repository": "owner/$(false)", "github_branch": "main;false",
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
            for args, expected_count in (([], 3), (["resume-batch"], 2), (["--roles-only", "resume-batch"], 1)):
                log.write_text("")
                subprocess.run(["bash", str(script), *args], env=env, check=True, capture_output=True)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                executions = [x for x in calls if x[:3] == ["run", "jobs", "execute"]]
                self.assertEqual(len(executions), expected_count)
                self.assertTrue(all(x[3] == "counter-presencial-vnext-gpu" for x in executions))
                modes = [next(x for x in call if x.startswith("--update-env-vars=")) for call in executions]
                if len(executions) > 1:
                    worker = next(call for call in executions if any("JOB_ROLE=worker," in x for x in call))
                    self.assertIn("--tasks=2", worker)
                else:
                    self.assertIn("JOB_ROLE=roles,", modes[0])
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
