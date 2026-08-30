from __future__ import annotations
import argparse, json, os
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]

def build_plan():
    """把 Git 中的治理定义转换成 deterministic DataHub governance URN 计划。
    
    输入：Domains、Owners、Tags、Structured Properties YAML。
    输出：PLAN_ONLY bootstrap payload，包含 domain / corpGroup / tag / structuredProperty URN。
    数据语义：Glossary 由独立 glossary ingestion 管理，避免在这里复制第二套 glossary authority。
    工程边界：这里只构造计划，不验证这些治理实体在 DataHub Runtime 中已经存在。"""
    domains=yaml.safe_load((ROOT/'metadata/datahub/governance/domains.yml').read_text()); owners=yaml.safe_load((ROOT/'metadata/datahub/governance/owners.yml').read_text()); tags=yaml.safe_load((ROOT/'metadata/datahub/governance/tags.yml').read_text()); props=yaml.safe_load((ROOT/'metadata/datahub/governance/structured_properties.yml').read_text())
    domain_items=[domains['root'],*domains.get('subdomains',[])]
    return {
      'contract':'commerce_datahub_governance_bootstrap','mode':'PLAN_ONLY','mutates_datahub':False,
      'glossary_bootstrap':{'owner':'metadata/datahub/governance/glossary.yml','handled_by':'datahub_glossary_ingestion'},
      'domains':[{**x,'urn':f"urn:li:domain:{x['id']}"} for x in domain_items],
      'groups':[{'id':x['id'],'urn':f"urn:li:corpGroup:{x['id']}",'display_name':x['display_name'],'description':x.get('purpose',''),'roles':x.get('roles',[])} for x in owners['groups']],
      'tags':[{'id':x['id'],'urn':f"urn:li:tag:{x['id']}",'display_name':x['display_name'],'description':x.get('description','')} for x in tags['tags']],
      'structured_properties':[{**x,'urn':f"urn:li:structuredProperty:{x['id']}"} for x in props['properties']],
    }

def main():
    """生成治理实体 bootstrap 计划，并对真实写入实行显式门禁。
    
    参数：--apply 决定是否尝试进入 mutation 路径。
    输出：默认写 governance_bootstrap_plan.json；apply 还要求 PHASE4C_ALLOW_DATAHUB_WRITE=true。
    工程边界：当前环境最终仍 DEFERRED，只有真实 workstation DataHub Runtime 才能产生写入证据。"""
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); args=ap.parse_args(); plan=build_plan(); out=ROOT/'metadata/datahub/generated/governance_bootstrap_plan.json'; out.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    if args.apply:
        if os.getenv('PHASE4C_ALLOW_DATAHUB_WRITE','false').lower()!='true': raise SystemExit('REFUSED: set PHASE4C_ALLOW_DATAHUB_WRITE=true before DataHub mutation.')
        raise SystemExit('DEFERRED: real DataHub governance bootstrap requires the workstation DataHub Runtime.')
    print(out)
if __name__=='__main__': main()
