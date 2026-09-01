#!/usr/bin/env python3
"""离线生成 Model Context Cards。

推荐在 CI / 发布流程执行：
    python scripts/build_model_context_cards.py

也可以只生成指定模型：
    python scripts/build_model_context_cards.py orders order_items
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.code_context import GovernedModelContextRepository  # noqa: E402


def main() -> int:
    models = tuple(sys.argv[1:]) or None
    repo = GovernedModelContextRepository(ROOT)
    written = repo.write_prebuilt(models)
    for path in written:
        print(path.relative_to(ROOT))
    print(f"generated={len(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
