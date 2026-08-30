from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.tools import GovernedMetadataTools

def main():
    p=argparse.ArgumentParser(description='Governed dimension-value discovery CLI'); p.add_argument('--metrics',required=True); p.add_argument('--dimension',required=True); p.add_argument('--question',default=''); p.add_argument('--limit',type=int,default=25); args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=GovernedMetadataTools(root).get_dimension_values(metrics=[x.strip() for x in args.metrics.split(',') if x.strip()],dimension=args.dimension,question=args.question,limit=args.limit)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
