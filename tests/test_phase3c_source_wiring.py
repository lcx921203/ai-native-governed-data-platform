from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"


class Phase3CSourceWiringTest(unittest.TestCase):
    def read(self, rel: str) -> str:
        text = (PKG / rel).read_text(encoding="utf-8")
        ast.parse(text, filename=rel)
        return text

    def test_three_dbt_groups_use_classified_adapter(self):
        text = self.read("assets/dbt.py")
        self.assertEqual(text.count("yield from execute_classified_dbt("), 3)
        self.assertIn('DBT_WINDOWED_SELECT = "tag:shopify_windowed"', text)

    def test_daily_job_excludes_global_staging_views(self):
        text = self.read("jobs.py")
        daily = text.split("shopify_daily_partition_job", 1)[1]
        self.assertNotIn("commerce_staging_dbt_assets", daily)

    def test_definitions_register_sensor_and_freshness(self):
        text = self.read("definitions.py")
        self.assertIn("shopify_daily_recovery_sensor", text)
        self.assertIn("apply_shopify_daily_freshness_policy", text)

    def test_no_free_text_failure_heuristic(self):
        text = self.read("failure_classification.py").lower()
        self.assertNotIn('"connection reset"', text)
        self.assertNotIn('"timeout error"', text)


if __name__ == "__main__":
    unittest.main()
