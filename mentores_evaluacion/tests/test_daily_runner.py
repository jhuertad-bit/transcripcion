import os
from datetime import date, datetime
import unittest
from unittest.mock import Mock, patch

from worker import main, catalogs


class DailyRunnerTests(unittest.TestCase):
    def test_daily_prepare_limits_candidates_to_yesterday_in_lima(self):
        client = Mock()
        client.query.return_value.result.return_value = []
        with patch.dict(os.environ, {"RUN_ID": "daily"}, clear=True), \
             patch("sys.argv", ["main.py", "prepare"]), \
             patch("worker.main.datetime") as clock, \
             patch("google.cloud.bigquery.Client", return_value=client), \
             patch.object(catalogs, "publish"):
            clock.now.return_value = datetime(2026, 11, 10, 7)
            main.main()
        self.assertEqual(str(clock.now.call_args.args[0]), "America/Lima")
        plan = next(c for c in client.query.call_args_list
                    if "INSERT INTO" in c.args[0] and "hist_mentores_execution_plan" in c.args[0])
        params = {p.name: p.value for p in plan.kwargs["job_config"].query_parameters}
        self.assertEqual(params["date_from"], date(2026, 11, 9))
        self.assertEqual(params["date_to"], date(2026, 11, 9))
        self.assertFalse(params["explicit_range"])
        self.assertIn("WHERE NOT @explicit_range AND DATE(c.fecha_audio)=@date_to", plan.args[0])

    def invoke(self, action):
        env = dict(DATE_FROM="2026-09-01", DATE_TO="2026-09-01", REPROCESS="true",
                   RUN_ID="workflow-day-1", CATALOG_LOAD_ID="a" * 32)
        client = Mock()
        client.query.return_value.result.return_value = []
        with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["main.py", action]), \
             patch("google.cloud.bigquery.Client", return_value=client), patch.object(catalogs, "publish") as publish:
            main.main()
        return client, publish

    def test_prepare_loads_catalogs_without_evaluating(self):
        client, publish = self.invoke("prepare")
        publish.assert_called_once()
        statements = [c.args[0] for c in client.query.call_args_list]
        self.assertFalse(any(s.startswith("CALL ") for s in statements))
        self.assertTrue(any(s.startswith("INSERT INTO") and "hist_mentores_execution_plan" in s for s in statements))

    def test_day_reuses_catalog_load_and_only_calls_procedure_once(self):
        client, publish = self.invoke("run")
        publish.assert_not_called()
        calls = [c for c in client.query.call_args_list if c.args[0].startswith("CALL ")]
        self.assertEqual(len(calls), 1)
        params = {p.name: p.value for p in calls[0].kwargs["job_config"].query_parameters}
        self.assertEqual(params["date_from"], params["date_to"])
        self.assertEqual(params["catalog_load_id"], "a" * 32)

    def test_multiple_days_cannot_be_sent_to_one_worker(self):
        env = dict(DATE_FROM="2026-09-01", DATE_TO="2026-09-03", REPROCESS="true", CATALOG_LOAD_ID="a" * 32)
        with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["main.py", "run"]), self.assertRaises(ValueError):
            main.main()
