from __future__ import annotations
import argparse, hashlib, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAIRS = [
    (ROOT/'infra/contracts/phase5/semantic_query_policy.yml', ROOT/'agent/contracts/semantic_query_policy.yml'),
    (ROOT/'infra/contracts/phase5/tool_schemas.json', ROOT/'agent/contracts/tool_schemas.json'),
    (ROOT/'infra/contracts/phase5/canonical_sources/semantic_query_planner.py', ROOT/'agent/semantic_query/planner.py'),
    (ROOT/'infra/contracts/phase5/canonical_sources/semantic_query_executor.py', ROOT/'agent/semantic_query/executor.py'),
    (ROOT/'infra/contracts/phase5/canonical_sources/analysis_session.py', ROOT/'agent/analysis_session/session.py'),
]

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--repair', action='store_true'); args=ap.parse_args()
    drift=[]
    for src,dst in PAIRS:
        if not dst.exists() or src.read_bytes()!=dst.read_bytes():
            drift.append((src,dst))
    if drift and args.repair:
        for src,dst in drift:
            dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src,dst)
            print(f'REPAIRED {dst.relative_to(ROOT)} <- {src.relative_to(ROOT)}')
        drift=[]
    if drift:
        for src,dst in drift:
            print(f'DRIFT {dst.relative_to(ROOT)} expected_sha256={digest(src)} actual_sha256={digest(dst) if dst.exists() else "MISSING"}')
        return 2
    print('Phase 5 canonical contract materialization: PASS')
    return 0
if __name__=='__main__': raise SystemExit(main())
