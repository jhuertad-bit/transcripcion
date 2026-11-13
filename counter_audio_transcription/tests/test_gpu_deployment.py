import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts.deploy_gpu import gpu_values, SOURCE
from worker.gpu_main import check_gpu


def source_job():
    values = dict(MODEL_DEVICE='cpu', ENABLE_DIARIZATION='true',
                  HF_TOKEN='private-test-placeholder',
                  ALLOWED_INPUT_BUCKETS='input', BQ_SOURCE_TABLE='prd-utpbi-data-operation.raw.entrada',
                  BQ_RESULTS_TABLE='prd-utpbi-data-operation.raw.resultados', BQ_LOCATION='US',
                  OUTPUT_GCS_ROOT='gs://output/counter/results',
                  MANIFEST_GCS_ROOT='gs://output/counter/manifests', NOISE_REDUCTION='true')
    return {'metadata': {'name': SOURCE}, 'spec': {'template': {'spec': {'template': {'spec': {
        'serviceAccountName': 'worker@project.iam.gserviceaccount.com',
        'containers': [{'env': [{'name': k, 'value': v} for k, v in values.items()]}]}}}}}}


class CounterGPUDeploymentTests(unittest.TestCase):
    def test_destinations_separate_without_modifying_cpu_or_copying_token(self):
        source = source_job()
        original = copy.deepcopy(source)
        env, tables, account = gpu_values(source)
        self.assertEqual(source, original)
        self.assertNotIn('HF_TOKEN', env)
        self.assertEqual(env['MODEL_DEVICE'], 'cuda')
        self.assertEqual(env['WHISPER_COMPUTE_TYPE'], 'float16')
        self.assertEqual(env['NOISE_REDUCTION'], 'true')
        self.assertEqual(env['BQ_LOCATION'], 'US')
        self.assertTrue(env['BQ_RESULTS_TABLE'].endswith('_gpu'))
        self.assertTrue(env['OUTPUT_GCS_ROOT'].endswith('/gpu'))
        self.assertEqual(env['FILES_PER_WORKER'], '1')
        self.assertEqual(tables[0][1], env['BQ_RESULTS_TABLE'])

    def test_wrong_source_rejected(self):
        source = source_job()
        source['metadata']['name'] = 'production'
        with self.assertRaises(ValueError):
            gpu_values(source)

    @patch.dict(os.environ, {'MODEL_DEVICE': 'cuda', 'WHISPER_COMPUTE_TYPE': 'float16'})
    def test_no_cpu_fallback(self):
        with self.assertRaises(RuntimeError):
            check_gpu(SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
                      SimpleNamespace())
