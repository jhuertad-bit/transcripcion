from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class DailyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / "workflows" / "daily_pipeline.yaml").read_text(encoding="utf-8"))
        self.steps = {key: value for step in self.workflow["main"]["steps"] for key, value in step.items()}

    def test_catalogs_precede_sequential_days_and_snapshot_is_pinned(self):
        self.assertLess(list(self.steps).index("prepare_catalogs_and_plan"), list(self.steps).index("each_day"))
        self.assertEqual(self.steps["prepare_catalogs_and_plan"]["args"]["action"], "prepare")
        loop = self.steps["each_day"]
        self.assertNotIn("parallel", loop)
        action = loop["for"]["steps"][0]["process_day"]
        self.assertEqual(action["args"]["date_from"], action["args"]["date_to"])
        self.assertEqual(action["args"]["snapshot_id"], "${row.f[1].v}")
        self.assertIn("row.f[0].v", action["args"]["run_id"])
        self.assertNotIn("try", action)
        self.assertNotIn("retry", action)

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
