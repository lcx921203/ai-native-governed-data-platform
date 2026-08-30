from __future__ import annotations
import json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]

def main():
    """把“期望治理策略”与“已解析 Dataset 身份”组合成治理投影。
    
    业务逻辑：逐个 consumer asset 合并 Domain、Owner、Tag、Glossary Term 与 Structured Property。
    输入 / 输出：读取 asset_policy.yml + dataset_identity_resolution.json，输出 governance_projection.json。
    DataHub 语义：只有 resolved_urn 存在时状态才是 READY；expected_urn 不能直接进入可写 dataset_urn。
    工程边界：本函数只生成投影，不调用 DataHub API，也不产生治理写入。"""
    policy=yaml.safe_load((ROOT/'metadata/datahub/governance/asset_policy.yml').read_text())
    identities=json.loads((ROOT/'metadata/datahub/generated/dataset_identity_resolution.json').read_text())
    by_model={x['model']:x for x in identities['identities']}
    rows=[]
    for asset in policy['assets']:
        identity=by_model[asset['model']]; resolved=identity.get('resolved_urn')
        rows.append({
          'model':asset['model'],
          'status':'READY' if resolved else 'BLOCKED_IDENTITY_UNRESOLVED',
          'dataset_urn':resolved,
          'expected_urn':identity['expected_urn'],
          'domain':asset['domain'],
          'owners':policy['defaults']['owners'],
          'tags':list(dict.fromkeys([*policy['defaults'].get('tags',[]),*asset.get('tags',[])])),
          'glossary_terms':asset.get('glossary_terms',[]),
          'structured_properties':{**policy['defaults'].get('structured_properties',{}),**asset.get('structured_properties',{})},
        })
    payload={'contract':'commerce_governance_projection','mutates_datahub':False,'items':rows}
    out=ROOT/'metadata/datahub/generated/governance_projection.json'; out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n'); print(out)
if __name__=='__main__': main()
