#!/usr/bin/env python3
"""查询本地 Agent JSONL Audit Store。

示例：
    python scripts/query_agent_audit.py --tenant-id tenant-west
    python scripts/query_agent_audit.py --event-type API_GUARD --status REQUEST_TIMEOUT
    python scripts/query_agent_audit.py --trace-id <trace_id>

只输出结构化 Audit Record，不会读取/恢复原始 Prompt 或 Answer。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.audit import GovernedAuditReader  # noqa: E402


def main() -> int:
    """解析结构化过滤条件并输出最近 Audit Record。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--subject", default="")
    parser.add_argument("--event-type", default="")
    parser.add_argument("--intent", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    rows = GovernedAuditReader(ROOT).query(
        trace_id=args.trace_id,
        tenant_id=args.tenant_id,
        subject=args.subject,
        event_type=args.event_type,
        intent=args.intent,
        runtime_status=args.status,
        since=args.since,
        max_results=args.limit,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
