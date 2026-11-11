import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts.render_job_env import build_values
from worker.gpu_main import check_gpu

ROOT = Path(__file__).resolve().parents[1]


class GPUDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.build = (ROOT/'cloudbuild.yaml').read_text()
        block = self.build.split('substitutions:\n', 1)[1].split('steps:', 1)[0]
        self.env = {k.strip(): json.loads(v.strip()) for k, v in
                    (line.split(':', 1) for line in block.splitlines() if line.strip())}
        self.template = (ROOT/'config/job.env.example.yaml').read_text()

    def test_gpu_destinations_and_cuda_are_independent(self):
        values = build_values(self.template, self.env)
        self.assertEqual(values['MODEL_DEVICE'], 'cuda')
        self.assertEqual(values['WHISPER_COMPUTE_TYPE'], 'float16')
        self.assertEqual(values['BQ_LOCATION'], 'US')
        for key in ('BQ_RESULTS_TABLE', 'BQ_ATTEMPTS_TABLE'):
            self.assertTrue(values[key].endswith('_gpu'))
        self.assertIn('prospeccion_gpu', values['OUTPUT_GCS_ROOT'])
        script = (ROOT/'scripts/cloudbuild_deploy.sh').read_text()
        self.assertIn('--no-gpu-zonal-redundancy', script)
        self.assertIn('--labels=proyecto=prospeccion', script)
        self.assertNotIn('LIKE ', (ROOT/'sql/01_create_transcripciones.sql').read_text())
        self.assertIn('worker/Dockerfile', self.build)

    def test_rejects_cpu_region_names_or_excess_parallelism(self):
        for change in ({'_LOCATION': 'us-central1'}, {'_JOB_NAME': 'cpu'},
                       {'_WORKFLOW_NAME': 'cpu'}, {'_PARALLELISM': '11'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_values(self.template, {**self.env, **change})

    @patch.dict(os.environ, {'MODEL_DEVICE': 'cuda', 'WHISPER_COMPUTE_TYPE': 'float16'})
    def test_gpu_preflight_rejects_missing_cuda(self):
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        ct2 = SimpleNamespace(get_supported_compute_types=lambda device: {'float16'})
        with self.assertRaises(RuntimeError):
            check_gpu(torch, ct2)
