from pathlib import Path
import re
import unittest

from scripts.prepare_sql import harden_response_contract

ROOT = Path(__file__).resolve().parents[1]


class ResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.sql = (ROOT / "sql" / "02_sp_process.sql").read_text(encoding="utf-8")
        self.schema = re.search(r"'''([\s\S]*?)''' AS output_schema", self.sql).group(1)

    def test_all_39_output_fields_are_required(self):
        fields = re.findall(r"^\s*([a-z0-9_]+) (FLOAT64|STRING) NOT NULL\s*,?\s*$", self.schema, re.M)
        self.assertEqual(len(fields), 39)
        self.assertEqual(sum(n.endswith("_calif") for n, _ in fields), 15)
        self.assertEqual(sum(n.endswith("_motivo") for n, _ in fields), 15)

    def test_all_califications_and_motives_are_checked_before_publication(self):
        calls = re.findall(r"subatributos_completos_mentor\(\[([\s\S]*?)\]\)", self.sql)
        self.assertEqual(len(calls), 2)
        expected = set(re.findall(r"^\s*([a-z0-9_]+_calif) STRING NOT NULL", self.schema, re.M))
        for call in calls:
            actual = set(re.findall(r"STRUCT\(([a-z0-9_]+_calif) AS calificacion", call))
            self.assertEqual(actual, expected)
            for calif in expected:
                self.assertIn(calif[:-len("_calif")] + "_motivo AS motivo", call)
        self.assertIn("OR NULLIF(TRIM(detalle.motivo), '') IS NULL", self.sql)
        self.assertIn("NOT IN ('1', '0', 'NA')", self.sql)

    def test_regeneration_does_not_duplicate_validation(self):
        self.assertEqual(harden_response_contract(self.sql), self.sql)
        self.assertEqual(self.sql.count("CREATE TEMP FUNCTION subatributos_completos_mentor("), 1)

    def test_schema_drift_is_rejected(self):
        broken = self.sql.replace("puntaje_total FLOAT64 NOT NULL,", "", 1)
        with self.assertRaisesRegex(ValueError, "39 columnas"):
            harden_response_contract(broken)
