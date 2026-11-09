import os
import unittest
from unittest.mock import Mock, patch

from worker import main, catalogs


class DailyRunnerTests(unittest.TestCase):
    def invoke(self, action):
        env = dict(DATE_FROM="2026-09-01", DATE_TO="2026-09-01", REPROCESS="true",
                   RUN_ID="workflow-day-1", SNAPSHOT_ID="a" * 32)
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

    def test_day_reuses_snapshot_and_only_calls_procedure_once(self):
        client, publish = self.invoke("run")
        publish.assert_not_called()
        calls = [c for c in client.query.call_args_list if c.args[0].startswith("CALL ")]
        self.assertEqual(len(calls), 1)
        params = {p.name: p.value for p in calls[0].kwargs["job_config"].query_parameters}
        self.assertEqual(params["date_from"], params["date_to"])
        self.assertEqual(params["snapshot_id"], "a" * 32)

    def test_multiple_days_cannot_be_sent_to_one_worker(self):
        env = dict(DATE_FROM="2026-09-01", DATE_TO="2026-09-03", REPROCESS="true", SNAPSHOT_ID="a" * 32)
        with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["main.py", "run"]), self.assertRaises(ValueError):
            main.main()
