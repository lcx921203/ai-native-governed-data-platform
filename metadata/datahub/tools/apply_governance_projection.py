from __future__ import annotations
import argparse, json, os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]

def main():
    """执行治理投影的 dry-run / apply 门禁。
    
    输入：generated governance_projection.json 与可选 --apply。
    输出：默认打印 DRY_RUN 计划；只有全部身份 READY 且显式写入环境门打开时才允许继续。
    DataHub API：真正的 Domain / Owner / Tag / Glossary / Structured Property mutation 属于 Runtime。
    工程边界：当前源码在真正 mutation 前仍返回 DEFERRED，不能用 dry-run 冒充 DataHub 写入成功。"""
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); args=ap.parse_args(); path=ROOT/'metadata/datahub/generated/governance_projection.json'
    if not path.exists(): raise SystemExit('Build governance projection first.')
    payload=json.loads(path.read_text()); blocked=[x for x in payload['items'] if x['status']!='READY']
    print(json.dumps({'mode':'APPLY' if args.apply else 'DRY_RUN','blocked':len(blocked),'items':payload['items']},ensure_ascii=False,indent=2))
    if not args.apply: return
    if blocked: raise SystemExit('REFUSED: unresolved Dataset identities block governance writes.')
    if os.getenv('PHASE4C_ALLOW_DATAHUB_WRITE','false').lower()!='true': raise SystemExit('REFUSED: set PHASE4C_ALLOW_DATAHUB_WRITE=true before DataHub mutation.')
    raise SystemExit('DEFERRED: real DataHub governance mutation requires the workstation DataHub Runtime.')
if __name__=='__main__': main()
