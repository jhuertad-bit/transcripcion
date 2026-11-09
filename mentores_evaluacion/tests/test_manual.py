import json
import unittest
from unittest.mock import patch
from scripts import run_manual


class ManualInvocationTests(unittest.TestCase):
    def test_impersonation_is_always_explicit(self):
        with patch("sys.argv", ["run_manual.py"]), patch.object(run_manual.subprocess, "run") as run:
            run_manual.main()
        argv = run.call_args.args[0]
        self.assertIn("--impersonate-service-account=" + run_manual.SERVICE_ACCOUNT, argv)
        self.assertIn("--data={}", argv)

    def test_range_is_forwarded_with_impersonation(self):
        with patch("sys.argv", ["run_manual.py", "--date-from", "2026-09-01", "--date-to", "2026-09-30", "--reprocess"]), patch.object(run_manual.subprocess, "run") as run:
            run_manual.main()
        argv = run.call_args.args[0]
        self.assertIn("--impersonate-service-account=" + run_manual.SERVICE_ACCOUNT, argv)
        payload = json.loads(next(a[7:] for a in argv if a.startswith("--data=")))
        self.assertEqual(payload, dict(date_from="2026-09-01", date_to="2026-09-30", reprocess=True))
