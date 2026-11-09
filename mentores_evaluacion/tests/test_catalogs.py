from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, patch

from worker import catalogs
from worker.normalizers import transformar


class CatalogTests(unittest.TestCase):
    def test_native_firestore_metadata_keeps_types_and_removes_download_token(self):
        doc = Mock()
        doc.reference.path = "mentor_charlas/m1/charlas/e1/audios/a1"
        doc.to_dict.return_value = {
            "mentorId": "m1", "activityid": "e1", "createdAt": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "remoteUrl": "https://firebasestorage.googleapis.com/v0/b/source-bucket/o/audios%2Fa1.m4a?token=private-token",
            "tiposCharlaMentor": ["Charla de auditorio"], "durationSeconds": 120.5,
        }
        row = transformar(catalogs.document(doc), "audios", "2026-11-09T00:00:00Z")
        self.assertEqual(row["source_gcs_uri"], "gs://source-bucket/audios/a1.m4a")
        self.assertEqual(row["duration_seconds"], 120.5)
        self.assertEqual(json.loads(row["tipos_charla_mentor_json"]), ["Charla de auditorio"])
        self.assertNotIn("private-token", json.dumps(row))
        self.assertEqual(row["created_at"], "2026-09-01T00:00:00+00:00")

    @patch("worker.catalogs.extract")
    def test_current_catalogs_are_replaced_together_after_all_loads(self, extract):
        extract.return_value = {kind: [{"test": "value"}] for kind in catalogs.TABLES}
        client, query = Mock(), Mock()
        catalogs.publish(client, "project.dataset", "a" * 32, "source", query)
        self.assertEqual(client.load_table_from_json.call_count, 3)
        transaction = query.call_args_list[-1].args[0]
        self.assertTrue(transaction.startswith("BEGIN TRANSACTION"))
        self.assertEqual(transaction.count("INSERT INTO"), 3)
        self.assertEqual(transaction.count("DELETE FROM"), 3)
        self.assertNotIn("hist_maestro", transaction)
        self.assertNotIn("hist_dias_utp", transaction)
        self.assertIn("SET catalog_loaded_at=CURRENT_TIMESTAMP()", transaction)
        self.assertNotIn("snapshot", transaction)
        for table in catalogs.TABLES.values():
            self.assertIn(f"DELETE FROM `project.dataset.{table}` WHERE TRUE;", transaction)
        self.assertIn("COMMIT TRANSACTION", transaction)
        self.assertEqual(client.delete_table.call_count, 3)

    @patch("worker.catalogs.extract")
    def test_failed_load_preserves_all_current_catalogs(self, extract):
        extract.return_value = {kind: [{"test": "value"}] for kind in catalogs.TABLES}
        client, query = Mock(), Mock()
        first, second = Mock(), Mock()
        second.result.side_effect = RuntimeError("fallo de carga")
        client.load_table_from_json.side_effect = [first, second]
        with self.assertRaises(RuntimeError):
            catalogs.publish(client, "project.dataset", "b" * 32, "source", query)
        self.assertFalse(any("BEGIN TRANSACTION" in c.args[0] for c in query.call_args_list))
        self.assertEqual(client.delete_table.call_count, 2)

    def test_init_creates_current_tables_without_replacing_existing_views(self):
        client, query = Mock(), Mock()
        catalogs.initialize(client, "project.dataset", query)
        query.assert_not_called()
        created = [c.args[0] for c in client.create_table.call_args_list]
        self.assertEqual({t.table_id for t in created}, set(catalogs.TABLES.values()))
        for table in created:
            self.assertIsNone(table.time_partitioning)
            self.assertIn("catalog_load_id", [f.name for f in table.schema])
            self.assertNotIn("snapshot_id", [f.name for f in table.schema])
        for call in client.create_table.call_args_list:
            self.assertTrue(call.kwargs["exists_ok"])


if __name__ == "__main__":
    unittest.main()
