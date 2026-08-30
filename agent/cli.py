from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.tools import GovernedMetadataTools

def main():
    p=argparse.ArgumentParser(description='Governed metadata read CLI')
    sub=p.add_subparsers(dest='cmd',required=True)
    m=sub.add_parser('metric'); m.add_argument('metric')
    e=sub.add_parser('entity'); e.add_argument('entity')
    d=sub.add_parser('dataset'); d.add_argument('dataset')
    l=sub.add_parser('lineage'); l.add_argument('dataset'); l.add_argument('--direction',choices=['upstream','downstream'],default='upstream'); l.add_argument('--max-hops',type=int,default=2)
    r=sub.add_parser('runtime'); r.add_argument('dataset')
    s=sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit',type=int,default=10)
    args=p.parse_args(); tools=GovernedMetadataTools(Path(__file__).resolve().parents[1])
    if args.cmd=='metric': out=tools.get_metric_context(metric=args.metric)
    elif args.cmd=='entity': out=tools.get_entity_context(entity=args.entity)
    elif args.cmd=='dataset': out=tools.get_dataset_context(dataset=args.dataset)
    elif args.cmd=='lineage': out=tools.get_lineage_context(dataset=args.dataset,direction=args.direction,max_hops=args.max_hops)
    elif args.cmd=='runtime': out=tools.get_runtime_context(dataset=args.dataset)
    else: out=tools.search_metadata(query=args.query,limit=args.limit)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
