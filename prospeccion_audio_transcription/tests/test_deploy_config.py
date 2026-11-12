import json
from pathlib import Path
import unittest
from scripts.render_job_env import build_values, RUNTIME_KEYS

ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT/'config/job.env.example.yaml').read_text(encoding='utf-8')
        self.build = (ROOT/'cloudbuild.yaml').read_text(encoding='utf-8')
        block = self.build.split('substitutions:\n', 1)[1].split('steps:', 1)[0]
        self.env = {k.strip(): json.loads(v.strip()) for k, v in
                    (line.split(':', 1) for line in block.splitlines() if line.strip())}

    def test_every_runtime_value_is_connected(self):
        values = build_values(self.template, self.env)
        for key in RUNTIME_KEYS:
            self.assertEqual(values[key], self.env['_' + key])
            self.assertIn(f"'_{key}=${{{'_' + key}}}'", self.build)

    def test_diarization_and_sources_can_be_overridden(self):
        self.env.update(_ENABLE_DIARIZATION='false')
        self.template = self.template.replace('MAX_SPEAKERS: "6"', 'MAX_SPEAKERS: "12"').replace(
            'prd-utpbi-data-operation.raw_cita_promotor.hist_cita_promotor_audio_catalog',
            'project.dataset.custom_catalog')
        values = build_values(self.template, self.env)
        self.assertEqual(values['ENABLE_DIARIZATION'], 'false')
        self.assertEqual(values['MAX_SPEAKERS'], '12')
        self.assertEqual(values['BQ_PROMOTOR_SOURCE_TABLE'], 'project.dataset.custom_catalog')

    def test_worker_and_workflow_share_manifest_root(self):
        script = (ROOT/'scripts/cloudbuild_deploy.sh').read_text()
        self.assertIn('MANIFEST_GCS_ROOT=${manifest_root}', script)
        self.assertIn('if [[ "${_ENABLE_DIARIZATION}" == false ]]', script)
        self.assertIn('"${secret_args[@]}"', script)

    def test_invalid_configuration_fails_before_deploy(self):
        for overrides in ({'_ENABLE_DIARIZATION': 'yes'}, {'_CPU': '2'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                build_values(self.template, {**self.env, **overrides})
        for old, new in (('BQ_LOCATION: "US"', 'BQ_LOCATION: "EU"'),
                         ('MAX_SPEAKERS: "6"', 'MAX_SPEAKERS: "0"')):
            with self.subTest(new=new), self.assertRaises(ValueError):
                build_values(self.template.replace(old, new), self.env)

    def test_table_is_sorted_and_defaults_match_template(self):
        self.assertEqual(list(self.env), sorted(self.env))
        original = {k: json.loads(v.strip()) for k, v in
                    (line.split(':', 1) for line in self.template.splitlines() if line.strip())}
        for key in RUNTIME_KEYS: self.assertEqual(original[key], self.env['_' + key])
