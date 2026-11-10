from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class DailyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / "workflows" / "daily_pipeline.yaml").read_text(encoding="utf-8"))
        self.steps = {key: value for step in self.workflow["main"]["steps"] for key, value in step.items()}

    def test_catalogs_precede_sequential_days_and_catalog_load_is_pinned(self):
        self.assertLess(list(self.steps).index("prepare_catalogs_and_plan"), list(self.steps).index("each_day"))
        self.assertEqual(self.steps["prepare_catalogs_and_plan"]["args"]["action"], "prepare")
        loop = self.steps["each_day"]
        self.assertNotIn("parallel", loop)
        action = loop["for"]["steps"][0]["process_day"]
        self.assertEqual(action["args"]["date_from"], action["args"]["date_to"])
        self.assertEqual(action["args"]["catalog_load_id"], "${row.f[1].v}")
        self.assertIn("row.f[0].v", action["args"]["run_id"])
        self.assertNotIn("try", action)
        self.assertNotIn("retry", action)
        self.assertEqual(action["call"], "process_day")
        self.assertEqual(action["args"]["daily"], "${daily}")

    def test_daily_has_exactly_two_sequential_non_forced_retries(self):
        steps = {k: v for step in self.workflow["process_day"]["steps"] for k, v in step.items()}
        self.assertEqual(steps["principal"]["call"], "run_job")
        self.assertEqual(steps["daily_retries"]["switch"],
                         [{"condition": "${daily}", "next": "retries"}])
        self.assertEqual(steps["daily_retries"]["next"], "done")
        loop = steps["retries"]["for"]
        self.assertEqual(loop["range"], [1, 2])
        self.assertNotIn("parallel", steps["retries"])
        retry = loop["steps"][0]["retry_pending"]
        self.assertEqual(retry["call"], "run_job")
        self.assertIs(retry["args"]["reprocess"], False)
        for key in ("job", "date_from", "date_to", "catalog_load_id"):
            self.assertEqual(retry["args"][key], steps["principal"]["args"][key])
        self.assertIn('"_retry_" + string(attempt)', retry["args"]["run_id"])
        init = self.steps["init"]["assign"]
        self.assertIn({"daily": '${date_from == "" and date_to == ""}'}, init)

    def test_retries_reuse_completed_results_and_limit_inference_to_pending_errors(self):
        sql = (ROOT / "sql" / "02_sp_process.sql").read_text(encoding="utf-8")
        self.assertIn("WHEN S.estado_proceso = 'CARGADO' THEN 'CARGADO'", sql)
        candidates = sql.split("CREATE TEMP TABLE tmp_mentores_audios_gemini", 1)[1].split("SET v_cantidad_audios_gemini", 1)[0]
        self.assertIn("estado_proceso IN ('PENDIENTE', 'ERROR')", candidates)
        self.assertIn("WHERE uri IN (SELECT uri FROM `_SESSION.tmp_mentores_control_universo`)", candidates)

    def test_pagination_and_polling_do_not_drop_dates(self):
        self.assertEqual(self.steps["wait_plan"]["next"], "check_plan_ready")
        self.assertEqual(self.steps["next_page"]["next"], "each_day")
        query = self.steps["read_plan"]["args"]["body"]["query"]
        self.assertIn("ORDER BY process_date", query)
        self.assertIn("WHERE execution_id=@execution_id", query)

    def test_job_connector_waits_before_next_day(self):
        run = self.workflow["run_job"]["steps"][0]["execute"]
        self.assertEqual(run["call"], "googleapis.run.v2.projects.locations.jobs.run")
        self.assertEqual(run["args"]["connector_params"]["timeout"], 28800)
        container = run["args"]["body"]["overrides"]["containerOverrides"][0]
        self.assertIn("action", container["args"])
        self.assertEqual(run["args"]["body"]["overrides"]["taskCount"], 1)
