"""Phase 7D End-to-End Runtime Evidence Aggregator（最终运行证据聚合器）。

它不启动业务任务，也不“推测”系统已经可用；只读取 ``phase7_final_closure.yml`` 明确列出的
Contract 中声明的全部 Runtime Evidence，并逐项检查文件存在、``runtime_verified=true`` 与 expected status 完全匹配。
缺任何一份证据，最终状态都必须保持 ``PHASE7_END_TO_END_RUNTIME_DEFERRED``。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "infra/contracts/phase7/phase7_final_closure.yml"


def main() -> int:
    """聚合所有 Phase 7 Required Runtime Evidence，并生成最终闭环证据。

    输入：``phase7_final_closure.yml`` 中声明的 evidence path + expected status。
    输出：``.runtime/evidence/phase7/final_runtime.json``。

    工程边界：这里没有 partial threshold（部分通过阈值）。Contract 中所有组件必须全部 VERIFIED，
    才能产生 ``PHASE7_END_TO_END_RUNTIME_VERIFIED``；静态源码或单个组件成功都不够。
    """
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    results: dict[str, dict] = {}
    all_passed = True

    for name, spec in contract["required_evidence"].items():
        path = ROOT / spec["path"]
        if not path.exists():
            results[name] = {"passed": False, "reason": "EVIDENCE_MISSING"}
            all_passed = False
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        passed = payload.get("runtime_verified") is True and payload.get("status") == spec["status"]
        results[name] = {
            "passed": passed,
            "expected_status": spec["status"],
            "observed_status": payload.get("status"),
            "source": str(path.relative_to(ROOT)),
        }
        all_passed = all_passed and passed

    payload = {
        "contract": "commerce_phase7_final_runtime",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": all_passed,
        "status": "PHASE7_END_TO_END_RUNTIME_VERIFIED" if all_passed else "PHASE7_END_TO_END_RUNTIME_DEFERRED",
        "components": results,
        "authority": contract["authority_audit"],
        "forbidden_capabilities": contract["forbidden"],
    }
    output = ROOT / ".runtime/evidence/phase7/final_runtime.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
