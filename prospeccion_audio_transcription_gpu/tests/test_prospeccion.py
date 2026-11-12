import copy
import json
import os
import unittest
from functools import partial
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from worker.catalog import metadata, SOURCES, prepare, table_id
from worker.config import ModelConfig
from worker.main import process_item
from worker.pipeline import run
from worker.bigquery_sink import BigQuerySink, AttemptSink
from test_counter import MemoryRepository


ENV = {
    'TIPO_ORIGEN': 'promotor', 'BQ_SOURCE_LOCATION': 'US',
    'ALLOWED_INPUT_BUCKETS': 'input-bucket', 'OUTPUT_GCS_ROOT': 'gs://output/prospeccion',
    'MANIFEST_GCS_ROOT': 'gs://output/manifests', 'BATCH_ID': 'batch-1',
    'BATCH_MAX_FILES': '1', 'FILES_PER_WORKER': '3',
    'PROCESS_DAY_FROM': '2026-11-09', 'PROCESS_DAY_TO': '2026-11-09',
}
API = SimpleNamespace(QueryJobConfig=lambda **kw: kw, ScalarQueryParameter=lambda *a: a)
MODULES = {'google': SimpleNamespace(cloud=SimpleNamespace(bigquery=API)),
           'google.cloud': SimpleNamespace(bigquery=API), 'google.cloud.bigquery': API}


def row(name='a.m4a'):
    return {'gcs_uri': 'gs://input-bucket/' + name, 'file_name': name, 'folder_uuid': '2437',
            'fecha_audio': '2026-11-09', 'fecha_procesamiento': '2026-11-10T15:17:03+00:00',
            'source_gcs_uri': 'gs://original/' + name, 'copy_result': 'copied', 'duration_seconds': 12.5}


class CatalogTests(unittest.TestCase):
    def test_identity_is_stable_and_separated_by_origin(self):
        p, m = metadata(row(), 'promotor'), metadata(row(), 'mentor')
        self.assertNotEqual(p['recording_id'], m['recording_id'])
        self.assertEqual(p['recording_id'], metadata(row(), 'promotor')['recording_id'])
        self.assertEqual(m['folder_uuid'], '2437')
        self.assertEqual(m['speaker_scope'], 'recording')
        self.assertEqual(m['duration_seconds'], 12.5)

    def test_rejects_unknown_origin_and_table_injection(self):
        with self.assertRaises(ValueError): metadata(row(), 'counter')
        with self.assertRaises(ValueError): table_id('project.dataset.table`; DELETE')

    @patch.dict(os.environ, ENV, clear=True)
    def test_prepare_uses_real_catalog_fields_and_skips_before_limit(self):
        bq, storage = MagicMock(), MagicMock()
        bq.query.return_value.result.return_value = [row('a.m4a'), row('b.m4a')]
        first, second = MemoryRepository(), MemoryRepository()
        first.read_json = lambda *a: {'result_uri': 'done'}
        with patch.dict('sys.modules', MODULES), patch('worker.catalog.GCSRepository', side_effect=[first, second]) as repos:
            prepare(storage, bq)
        sql = bq.query.call_args.args[0]
        self.assertIn(SOURCES['promotor'], sql)
        self.assertIn('fecha_audio >= @day_from', sql)
        self.assertIn('already_in_gcs', sql)
        self.assertNotIn('load_date', sql)
        self.assertNotIn('process_day', sql)
        manifest = json.loads(storage.bucket.return_value.blob.return_value.upload_from_string.call_args_list[0].args[0])
        self.assertEqual(manifest['count'], 1)
        self.assertEqual(manifest['task_count'], 1)
        self.assertEqual(manifest['items'][0]['catalog']['file_name'], 'b.m4a')
        self.assertEqual(repos.call_args.args[2], 'gs://output/prospeccion/promotor')
        self.assertIn('promotor/batch-1.json', storage.bucket.return_value.blob.call_args_list[0].args[0])

    @patch.dict(os.environ, {**ENV, 'TIPO_ORIGEN': 'mentor'}, clear=True)
    def test_mentor_selects_mentor_table(self):
        bq, storage = MagicMock(), MagicMock()
        bq.query.return_value.result.return_value = []
        with patch.dict('sys.modules', MODULES): prepare(storage, bq)
        self.assertIn(SOURCES['mentor'], bq.query.call_args.args[0])

    @patch.dict(os.environ, {**ENV, 'PROCESS_DAY_FROM': ''}, clear=True)
    def test_requires_dates(self):
        with patch.dict('sys.modules', MODULES), self.assertRaises(ValueError): prepare(MagicMock(), MagicMock())

    @patch.dict(os.environ, {**ENV, 'BQ_SOURCE_TABLE': SOURCES['mentor']}, clear=True)
    def test_rejects_mismatched_catalog(self):
        with patch.dict('sys.modules', MODULES), self.assertRaises(ValueError): prepare(MagicMock(), MagicMock())


class AttemptTests(unittest.TestCase):
    def setUp(self):
        c = metadata(row(), 'promotor')
        c['source_table'] = SOURCES['promotor']
        self.repo = MemoryRepository()
        self.repo.source = {'uri': c['gcs_uri'], 'generation': '7', 'size_bytes': 100}
        self.item = {'audio_uri': c['gcs_uri'], 'recording_id': c['recording_id'], 'catalog': c,
                     'source': copy.deepcopy(self.repo.source)}
        self.attempts = MagicMock()

    def execute(self, func=None, factory=None):
        return process_item(self.item, None, ModelConfig(), MagicMock(), self.attempts,
                            execute=func or (lambda *a, **kw: {'status': 'completed', 'result_uri': 'gs://output/result.json'}),
                            repository_factory=factory or (lambda *a: self.repo))

    @patch.dict(os.environ, ENV, clear=True)
    def test_tracks_queue_processing_and_success(self):
        self.execute()
        values = [c.args[0] for c in self.attempts.write.call_args_list]
        self.assertEqual([v['estado'] for v in values], ['queued', 'processing', 'completed'])
        self.assertEqual(len({v['attempt_id'] for v in values}), 1)
        self.assertIsNotNone(values[-1]['finished_at'])

    @patch.dict(os.environ, ENV, clear=True)
    def test_repository_failure_is_tracked(self):
        def fail(*a): raise FileNotFoundError('missing audio')
        with self.assertRaises(FileNotFoundError): self.execute(factory=fail)
        self.assertEqual(self.attempts.write.call_args.args[0]['estado'], 'failed')
        self.assertEqual(self.attempts.write.call_args.args[0]['error_type'], 'FileNotFoundError')

    @patch.dict(os.environ, ENV, clear=True)
    def test_generation_change_does_not_infer(self):
        self.repo.source['generation'] = '8'
        func = MagicMock()
        with self.assertRaises(ValueError): self.execute(func=func)
        func.assert_not_called()
        self.assertEqual(self.attempts.write.call_args.args[0]['estado'], 'failed')

    @patch.dict(os.environ, ENV, clear=True)
    def test_different_attempts_preserve_key(self):
        self.execute(); self.execute()
        terminal = [x.args[0] for x in self.attempts.write.call_args_list if x.args[0]['estado'] == 'completed']
        self.assertNotEqual(terminal[0]['attempt_id'], terminal[1]['attempt_id'])
        self.assertEqual(terminal[0]['processing_key'], terminal[1]['processing_key'])

    @patch.dict(os.environ, ENV, clear=True)
    def test_failed_control_write_prevents_inference(self):
        self.attempts.write.side_effect = RuntimeError('BQ unavailable')
        func = MagicMock()
        with self.assertRaises(RuntimeError): self.execute(func=func)
        func.assert_not_called()

    @patch.dict(os.environ, ENV, clear=True)
    def test_stage_failure_is_preserved(self):
        def fail(*a, **kw):
            kw['progress']('diarizacion')
            raise RuntimeError('model error')
        with self.assertRaises(RuntimeError): self.execute(func=fail)
        self.assertEqual(self.attempts.write.call_args.args[0]['etapa'], 'diarizacion')

    @patch.dict(os.environ, ENV, clear=True)
    def test_real_pipeline_correlates_result_and_attempt(self):
        execute = partial(run, normalize_audio=lambda *a, **kw: 2.0,
                          transcribe_audio=lambda *a: {'words': [{'word': ' Hola', 'start': 0, 'end': 1}],
                              'timestamp_gaps': [], 'language': 'es', 'language_probability': 1},
                          diarize_audio=lambda *a: {'regular': [{'speaker_id': 'SPEAKER_00', 'start': 0, 'end': 2}],
                              'exclusive': [{'speaker_id': 'SPEAKER_00', 'start': 0, 'end': 2}]})
        with patch('worker.pipeline.tempfile.TemporaryDirectory', return_value=nullcontext('test-path')):
            outcome = self.execute(func=execute)
        attempt = self.attempts.write.call_args.args[0]
        completed = self.repo.read_json(outcome['processing_key'], 'completed.json')
        result = self.repo.read_json(outcome['processing_key'], completed['result_path'])
        self.assertEqual(result['attempt_id'], attempt['attempt_id'])
        self.assertEqual(result['source_system'], 'prospeccion')
        self.assertEqual(result['catalog']['tipo_origen'], 'promotor')
        self.assertEqual(result['transcription'][0]['speaker_id'], 'SPEAKER_00')
        stages = [c.args[0]['etapa'] for c in self.attempts.write.call_args_list]
        self.assertIn('transcripcion', stages)
        self.assertIn('diarizacion', stages)
        self.assertEqual(stages[-1], 'publicacion')


class SinkTests(unittest.TestCase):
    def test_results_match_existing_ddl_and_json_is_parameterized(self):
        c = metadata(row(), 'promotor'); c['source_table'] = SOURCES['promotor']
        result = {'catalog': c, 'models': ModelConfig().as_dict(), 'processing_key': 'key',
                  'recording_id': c['recording_id'], 'source': {'uri': c['gcs_uri'], 'generation': '7'},
                  'duration_seconds': 12.5, 'language': 'es', 'serialized_level_1': "texto ' especial",
                  'transcription': [], 'processed_at': '2026-11-10T16:00:00Z', 'elapsed_seconds': 2.0}
        client = MagicMock()
        with patch.dict('sys.modules', MODULES):
            BigQuerySink(client, 'project.dataset.results', 'US').write(result, 'gs://output/result.json')
        sql = client.query.call_args.args[0]
        self.assertIn('T.processing_key=S.processing_key', sql)
        self.assertNotIn("texto ' especial", sql)
        params = client.query.call_args.kwargs['job_config']['query_parameters']
        ddl = (Path(__file__).resolve().parents[1]/'sql/01_create_transcripciones.sql').read_text()
        for name, _, _ in params: self.assertIn(name + ' ', ddl)
        self.assertEqual(dict((n, v) for n, _, v in params)['folder_uuid'], '2437')
        self.assertIn("PARSE_JSON(@serialized_level_2, wide_number_mode=>'round')", sql)
        self.assertIn('PARSE_JSON(@catalog_metadata)', sql)

    def test_attempt_merge_keys_on_attempt_not_recording(self):
        client = MagicMock()
        with patch.dict('sys.modules', MODULES):
            AttemptSink(client, 'project.dataset.attempts', 'US').write({'attempt_id': 'a', 'estado': 'failed'})
        self.assertIn('T.attempt_id=S.attempt_id', client.query.call_args.args[0])


if __name__ == '__main__': unittest.main()
