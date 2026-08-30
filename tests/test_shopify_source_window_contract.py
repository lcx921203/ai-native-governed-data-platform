from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOPIFY = ROOT / "ingestion" / "shopify"
sys.path.insert(0, str(SHOPIFY))

from source_window import SourceWindow  # noqa:E402


class ShopifySourceWindowContractTest(unittest.TestCase):
    def test_half_open_boundary(self):
        window = SourceWindow.from_cli_values(
            "2026-08-04T23:55:00Z",
            "2026-08-06T00:00:00Z",
        )
        assert window is not None
        self.assertTrue(window.contains("2026-08-05T12:05:00Z"))
        self.assertTrue(window.contains("2026-08-05T16:00:00Z"))
        self.assertFalse(window.contains("2026-08-06T00:00:00Z"))
        self.assertFalse(window.contains("2026-08-03T10:00:00Z"))

    def test_aug05_fixture_selection(self):
        window = SourceWindow.from_cli_values(
            "2026-08-04T23:55:00Z",
            "2026-08-06T00:00:00Z",
        )
        selected = []
        for path in sorted((ROOT / "data/fixtures/shopify").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if window and window.contains(payload["updatedAt"]):
                selected.append(path.name)
        self.assertEqual(
            selected,
            ["order_fulfillment.json", "order_partial_refund.json"],
        )

    def test_fixture_loader_parses_window(self):
        text = (SHOPIFY / "load_fixtures.py").read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn('"--window-start"', text)
        self.assertIn('"--window-end"', text)
        self.assertIn("fixture_in_window", text)

    def test_normalize_pushes_predicate_to_raw_scan(self):
        path = ROOT / "lakehouse/jobs/normalize_shopify_orders.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn('F.col("order_updated_at") >=', text)
        self.assertIn('F.col("order_updated_at") <', text)
        self.assertIn("window_start=args.window_start", text)
        self.assertIn("window_end=args.window_end", text)


if __name__ == "__main__":
    unittest.main()
