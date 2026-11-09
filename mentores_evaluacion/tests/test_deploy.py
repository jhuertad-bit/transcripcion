import os
import unittest
from unittest.mock import patch

from scripts import deploy


class ManualDeployTests(unittest.TestCase):
    def check_manual(self, existing):
        env = dict(PROJECT="prd-utpbi-data-operation", LOCATION="us-central1",
                   REPOSITORY="serializa-audio-repo", SERVICE_ACCOUNT="job@example.com",
                   WORKFLOW_SERVICE_ACCOUNT="workflow@example.com", SCHEDULE="0 7 * * *",
                   JOB_NAME="prd-utpbi-mentores-evaluacion",
                   WORKFLOW_NAME="prd-utpbi-mentores-evaluacion-pipeline",
                   SCHEDULER_NAME="prd-sch-mentores-evaluacion", SOURCE_DIR=".",
                   IMAGE_URI="image:test", BQ_LOCATION="US", FIRESTORE_SECRET="source")
        listing = '[{"name":"projects/p/locations/l/jobs/prd-sch-mentores-evaluacion"}]' if existing else '[]'
        with patch.dict(os.environ, env, clear=True), patch.object(deploy, "call", return_value=listing) as call:
            deploy.main()
        calls = [c.args for c in call.call_args_list]
        self.assertTrue(any(c[:2] == ("workflows", "deploy") for c in calls))
        self.assertFalse(any(c[:3] in (("scheduler", "jobs", "create"),
                                      ("scheduler", "jobs", "update"),
                                      ("scheduler", "jobs", "resume")) for c in calls))
        self.assertEqual(any(c[:3] == ("scheduler", "jobs", "pause") for c in calls), existing)
        executions = [c for c in calls if c[:3] == ("run", "jobs", "execute")]
        self.assertEqual(len(executions), 1)
        self.assertIn("--args=-m,worker.main,init", executions[0])

    def test_default_does_not_create_scheduler(self):
        self.check_manual(False)

    def test_default_pauses_existing_scheduler(self):
        self.check_manual(True)
