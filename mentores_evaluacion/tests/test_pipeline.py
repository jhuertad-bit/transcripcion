import ast
from datetime import date
from pathlib import Path
import unittest

from worker.main import parameters, PROCESS_CALL, PROCEDURE

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def test_reprocess_requires_explicit_valid_range(self):
        for env in ({"REPROCESS": "true"}, {"DATE_FROM": "2026-09-01"},
                    {"REPROCESS": "yes"}, {"DATE_FROM": "2026-09-02", "DATE_TO": "2026-09-01"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                parameters(env)

    def test_same_execution_has_same_id_and_dates(self):
        env = dict(REPROCESS="true", DATE_FROM="2026-09-01", DATE_TO="2026-09-30", RUN_ID="workflow-1")
        first, last, force, run = parameters(env)
        self.assertEqual((first, last, force), (date(2026, 9, 1), date(2026, 9, 30), True))
        self.assertEqual(run, parameters(env)[3])
        self.assertNotEqual(run, parameters(dict(env, RUN_ID="workflow-2"))[3])

    def test_work_tables_are_isolated_and_history_is_atomic(self):
        s = (ROOT / "sql" / "02_sp_process.sql").read_text(encoding="utf-8")
        self.assertNotIn("__RUN_SUFFIX__", s)
        self.assertNotIn("raw_genesys_audios", s)
        self.assertNotIn("mentores_manual", s)
        self.assertIn("CREATE TEMP TABLE tmp_mentores_control_universo", s)
        self.assertIn("`_SESSION.tmp_mentores_json`", s)
        self.assertIn("owner=p_lock_owner", s)
        self.assertLess(s.index("EJECUTAR_DESDE_WORKFLOW_CON_BLOQUEO"), s.index("CREATE TEMP TABLE"))
        self.assertLess(s.index("BEGIN TRANSACTION"), s.index("INSERT INTO `prd-utpbi-data-operation.raw_cita_promotor.hist_mentores_evaluaciones`"))
        self.assertLess(s.index("hist_mentores_evaluaciones`"), s.index("  MERGE\n    `prd-utpbi-data-operation.adf_speech_analytics"))
        self.assertIn("COMMIT TRANSACTION", s)
        self.assertIn("AND DATE(fecha_audio) >= p_date_from", s)
        self.assertNotIn("NOT p_reprocess OR DATE(fecha_audio)", s)
        self.assertIn("p_date_from = p_date_to", s)
        self.assertEqual(s.count("WHERE catalog_load_id=p_catalog_load_id"), 3)
        self.assertEqual(s.count("AS 'CATALOGO_CAMBIO_REINICIAR_WORKFLOW'"), 3)
        self.assertNotIn("hist_mentores_catalog_snapshots", s)
        self.assertNotIn("hist_dias_utp", s)
        self.assertIn("raw_cita_promotor.cat_dias_utp", s)
        self.assertNotIn("@date_from", s)

    def test_job_calls_persistent_procedure_with_bound_parameters(self):
        self.assertEqual(PROCESS_CALL, f"CALL `{PROCEDURE}`(@date_from, @date_to, @reprocess, @run_id, @owner, @catalog_load_id);")
        s = (ROOT / "sql" / "02_sp_process.sql").read_text(encoding="utf-8")
        self.assertIn(f"CREATE OR REPLACE PROCEDURE `{PROCEDURE}`", s)
        for name in ("p_date_from DATE", "p_date_to DATE", "p_reprocess BOOL", "p_run_id STRING", "p_lock_owner STRING", "p_catalog_load_id STRING"):
            self.assertIn("IN " + name, s)

    def test_init_does_not_replace_history(self):
        ddl = (ROOT / "sql" / "01_init.sql").read_text(encoding="utf-8")
        self.assertNotIn("CREATE OR REPLACE TABLE", ddl)
        self.assertNotIn("DROP TABLE", ddl)
        self.assertIn("hist_utp_gen_ia_results_process_data_mentores_prd", ddl)
        self.assertNotIn("hist_mentores_catalog_snapshots", ddl)
        self.assertIn("ADD COLUMN IF NOT EXISTS catalog_load_id", ddl)

    def test_no_local_credentials_or_audio_copy_in_runtime(self):
        for file in (ROOT / "worker").glob("*.py"):
            text = file.read_text(encoding="utf-8")
            ast.parse(text)
            self.assertNotIn("from_service_account_file", text)
            self.assertNotIn("print-access-token", text)
            self.assertNotIn("copy_blob", text)


if __name__ == "__main__":
    unittest.main()
