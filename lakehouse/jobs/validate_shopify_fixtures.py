"""Static validation for local Shopify fixture consistency.

This script intentionally has no Spark dependency. It catches fixture problems
before Raw ingestion, such as duplicate Shopify object IDs or nested references
that do not point to objects in the same order observation.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


FIXTURE_DIR = Path("data/fixtures/shopify")


def nodes(connection):
    if not connection:
        return []
    return connection.get("nodes") or []


def money_amount(obj, field):
    value = obj.get(field) or {}
    shop_money = value.get("shopMoney") or {}
    return shop_money.get("amount")


def validate():
    fixtures = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixtures.append((path, json.loads(path.read_text(encoding="utf-8"))))

    assert fixtures, f"no fixture files found under {FIXTURE_DIR}"

    unique_ids = {
        "order": [],
        "line_item": [],
        "transaction": [],
        "refund": [],
        "refund_line_item": [],
        "fulfillment": [],
        "fulfillment_line_item": [],
        "fulfillment_event": [],
    }

    counts = Counter()

    for path, order in fixtures:
        assert order.get("id"), f"{path.name}: missing order id"
        assert order.get("updatedAt"), f"{path.name}: missing updatedAt"
        assert order.get("currencyCode"), f"{path.name}: missing currencyCode"

        for field in (
            "originalTotalPriceSet",
            "currentTotalPriceSet",
            "currentTotalDiscountsSet",
            "totalRefundedSet",
        ):
            assert money_amount(order, field) is not None, (
                f"{path.name}: missing {field}.shopMoney.amount"
            )

        unique_ids["order"].append(order["id"])
        counts["orders"] += 1

        line_items = nodes(order.get("lineItems"))
        line_item_ids = {item["id"] for item in line_items}

        for item in line_items:
            unique_ids["line_item"].append(item["id"])
            counts["order_items"] += 1
            counts["discount_allocations"] += len(item.get("discountAllocations") or [])

        transactions = order.get("transactions") or []
        transaction_ids = {tx["id"] for tx in transactions}
        for tx in transactions:
            unique_ids["transaction"].append(tx["id"])
            counts["transactions"] += 1

        for refund in order.get("refunds") or []:
            unique_ids["refund"].append(refund["id"])
            counts["refunds"] += 1

            for ri in nodes(refund.get("refundLineItems")):
                unique_ids["refund_line_item"].append(ri["id"])
                counts["refund_items"] += 1
                referenced_line = (ri.get("lineItem") or {}).get("id")
                assert referenced_line in line_item_ids, (
                    f"{path.name}: RefundLineItem {ri['id']} references unknown "
                    f"LineItem {referenced_line}"
                )

            for refund_tx in nodes(refund.get("transactions")):
                counts["refund_transactions"] += 1
                tx_id = refund_tx.get("id")
                assert tx_id in transaction_ids, (
                    f"{path.name}: Refund {refund['id']} references transaction "
                    f"{tx_id}, but it is missing from order.transactions"
                )

        for fulfillment in order.get("fulfillments") or []:
            unique_ids["fulfillment"].append(fulfillment["id"])
            counts["fulfillments"] += 1

            for fi in nodes(fulfillment.get("fulfillmentLineItems")):
                unique_ids["fulfillment_line_item"].append(fi["id"])
                counts["fulfillment_items"] += 1
                referenced_line = (fi.get("lineItem") or {}).get("id")
                assert referenced_line in line_item_ids, (
                    f"{path.name}: FulfillmentLineItem {fi['id']} references "
                    f"unknown LineItem {referenced_line}"
                )

            for event in nodes(fulfillment.get("events")):
                unique_ids["fulfillment_event"].append(event["id"])
                counts["fulfillment_events"] += 1
                assert event.get("happenedAt"), (
                    f"{path.name}: FulfillmentEvent {event['id']} should have "
                    "happenedAt in the demo fixture"
                )

    for entity, values in unique_ids.items():
        duplicates = sorted(k for k, v in Counter(values).items() if v > 1)
        assert not duplicates, f"duplicate {entity} IDs: {duplicates}"

    expected = {
        "orders": 5,
        "order_items": 5,
        "discount_allocations": 1,
        "transactions": 7,
        "refunds": 1,
        "refund_items": 1,
        "refund_transactions": 1,
        "fulfillments": 1,
        "fulfillment_items": 1,
        "fulfillment_events": 2,
    }
    assert dict(counts) == expected, f"unexpected fixture counts: {dict(counts)}"

    print("fixture validation passed")
    for key, value in expected.items():
        print(f"  {key:24s} {value}")


if __name__ == "__main__":
    validate()
