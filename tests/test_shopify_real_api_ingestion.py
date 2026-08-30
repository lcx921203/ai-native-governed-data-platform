from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SHOPIFY = ROOT / "ingestion" / "shopify"
sys.path.insert(0, str(ROOT))

from ingestion.shopify import extract_orders  # noqa: E402
from ingestion.shopify.source_config import load_source_config  # noqa: E402


class ShopifySourceModeTest(unittest.TestCase):
    def test_environment_defaults_to_fixture(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHOPIFY_SOURCE_MODE", None)
            source = load_source_config()

        self.assertEqual(source.mode, "fixture")
        self.assertEqual(source.kind, "fixture")

    def test_environment_switches_to_production(self):
        with mock.patch.dict(
            os.environ,
            {
                "SHOPIFY_SOURCE_MODE": "production",
                "SHOPIFY_PAGE_SIZE": "125",
                "SHOPIFY_NESTED_PAGE_SIZE": "80",
            },
            clear=False,
        ):
            source = load_source_config()

        self.assertEqual(source.mode, "production")
        self.assertEqual(source.kind, "shopify_admin_graphql")
        self.assertEqual(source.get("page_size"), 125)
        self.assertEqual(source.get("nested_page_size"), 80)
        self.assertTrue(source.get("nested_pagination"))

    def test_unknown_source_mode_fails_closed(self):
        with mock.patch.dict(
            os.environ,
            {"SHOPIFY_SOURCE_MODE": "prodction"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                load_source_config()


class ShopifyCursorPaginationTest(unittest.TestCase):
    def test_first_is_page_size_and_after_uses_previous_end_cursor(self):
        seen_cursors: list[str] = []
        connection = {
            "nodes": [{"id": "A"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-A"},
        }

        def fetch_page(after: str):
            seen_cursors.append(after)
            return {
                "nodes": [{"id": "B"}],
                "pageInfo": {"hasNextPage": False, "endCursor": "cursor-B"},
            }

        result = extract_orders.append_connection_pages(connection, fetch_page=fetch_page)
        self.assertEqual(seen_cursors, ["cursor-A"])
        self.assertEqual([node["id"] for node in result["nodes"]], ["A", "B"])

    def test_all_project_nested_connections_are_completed(self):
        order = {
            "id": "gid://shopify/Order/1",
            "lineItems": {
                "nodes": [{"id": "line-1"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "line-c1"},
            },
            "refunds": [
                {
                    "id": "gid://shopify/Refund/1",
                    "refundLineItems": {
                        "nodes": [{"id": "refund-line-1"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "rli-c1"},
                    },
                    "transactions": {
                        "nodes": [{"id": "refund-tx-1"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "rtx-c1"},
                    },
                }
            ],
            "fulfillments": [
                {
                    "id": "gid://shopify/Fulfillment/1",
                    "fulfillmentLineItems": {
                        "nodes": [{"id": "fulfill-line-1"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "fli-c1"},
                    },
                    "events": {
                        "nodes": [{"id": "event-1"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "evt-c1"},
                    },
                }
            ],
        }

        def fake_nested(*, connection_name: str, after: str, **_kwargs):
            suffix = {
                "lineItems": "line-2",
                "refundLineItems": "refund-line-2",
                "transactions": "refund-tx-2",
                "fulfillmentLineItems": "fulfill-line-2",
                "events": "event-2",
            }[connection_name]
            return {
                "nodes": [{"id": suffix}],
                "pageInfo": {"hasNextPage": False, "endCursor": f"{after}-done"},
            }

        with mock.patch.object(
            extract_orders,
            "_request_nested_connection",
            side_effect=fake_nested,
        ):
            result = extract_orders.complete_nested_pagination(
                order,
                nested_page_size=100,
                request_options={},
            )

        self.assertEqual(len(result["lineItems"]["nodes"]), 2)
        self.assertEqual(len(result["refunds"][0]["refundLineItems"]["nodes"]), 2)
        self.assertEqual(len(result["refunds"][0]["transactions"]["nodes"]), 2)
        self.assertEqual(len(result["fulfillments"][0]["fulfillmentLineItems"]["nodes"]), 2)
        self.assertEqual(len(result["fulfillments"][0]["events"]["nodes"]), 2)

    def test_root_order_cursor_pagination_passes_first_and_after(self):
        calls: list[dict] = []

        first_page = {
            "orders": {
                "nodes": [{"id": "order-1", "lineItems": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}, "refunds": [], "fulfillments": []}],
                "pageInfo": {"hasNextPage": True, "endCursor": "order-c1"},
            }
        }
        second_page = {
            "orders": {
                "nodes": [{"id": "order-2", "lineItems": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}, "refunds": [], "fulfillments": []}],
                "pageInfo": {"hasNextPage": False, "endCursor": "order-c2"},
            }
        }

        def fake_graphql(_query, variables, **_options):
            calls.append(dict(variables))
            return first_page if variables["after"] is None else second_page

        with mock.patch.object(extract_orders, "graphql_request", side_effect=fake_graphql):
            rows = extract_orders.extract_orders_in_window(
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 2, tzinfo=timezone.utc),
                page_size=100,
                nested_page_size=100,
            )

        self.assertEqual([row["id"] for row in rows], ["order-1", "order-2"])
        self.assertEqual(calls[0]["first"], 100)
        self.assertIsNone(calls[0]["after"])
        self.assertEqual(calls[1]["after"], "order-c1")


class ShopifyGraphqlContractTest(unittest.TestCase):
    def test_root_query_exposes_page_info_for_every_nested_connection(self):
        text = (SHOPIFY / "queries/orders.graphql").read_text(encoding="utf-8")
        self.assertIn("lineItems(first: $nestedFirst)", text)
        self.assertIn("refundLineItems(first: $nestedFirst)", text)
        self.assertIn("transactions(first: $nestedFirst)", text)
        self.assertIn("fulfillmentLineItems(first: $nestedFirst)", text)
        self.assertIn("events(first: $nestedFirst", text)
        self.assertGreaterEqual(text.count("hasNextPage"), 5)
        self.assertGreaterEqual(text.count("endCursor"), 5)

    def test_nested_page_documents_accept_after_cursor(self):
        for name in [
            "order_line_items_page.graphql",
            "refund_line_items_page.graphql",
            "refund_transactions_page.graphql",
            "fulfillment_line_items_page.graphql",
            "fulfillment_events_page.graphql",
        ]:
            text = (SHOPIFY / "queries" / name).read_text(encoding="utf-8")
            self.assertIn("$after: String", text, name)
            self.assertIn("after: $after", text, name)
            self.assertIn("pageInfo", text, name)


class ShopifyProductionGuardTest(unittest.TestCase):
    def test_proactive_throttle_uses_runtime_cost_budget(self):
        payload = {
            "extensions": {
                "cost": {
                    "requestedQueryCost": 80,
                    "throttleStatus": {
                        "currentlyAvailable": 50,
                        "restoreRate": 100,
                    },
                }
            }
        }
        # 需要 80 + 20 reserve，目前 50，差 50；100 points/s -> 0.5s。
        delay = extract_orders._proactive_throttle_delay(payload, reserve_points=20)
        self.assertAlmostEqual(delay, 0.5)

    def test_api_version_mismatch_fails_closed(self):
        response = mock.Mock()
        response.headers = {"X-Shopify-API-Version": "2026-10"}
        with self.assertRaises(RuntimeError):
            extract_orders._verify_api_version(response, "2026-07")


if __name__ == "__main__":
    unittest.main()
