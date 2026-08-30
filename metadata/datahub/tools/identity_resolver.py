from __future__ import annotations
import argparse, json
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]

def expected_payload():
    """生成“预期 DataHub Dataset 身份”静态契约。
    
    业务逻辑：按 asset_policy 中的 canonical dbt model name 构造 Iceberg Dataset 名称与 expected URN。
    输入 / 输出：读取治理 YAML，输出 identities 列表；每项只有 expected_urn，resolved_urn 仍为 None。
    DataHub 语义：expected URN 只是 deterministic identity candidate，不代表 DataHub 里真的存在该 Dataset。
    工程边界：当前 phone/static 环境只能生成 UNVERIFIED_EXPECTED；不能把它升级成 RESOLVED。"""
    policy=yaml.safe_load((ROOT/'metadata/datahub/governance/asset_policy.yml').read_text())
    items=[]
    for asset in policy['assets']:
        model=asset['model']; name=f'commerce_polaris.analytics.{model}'
        items.append({
            'model':model,'relation_name':model,'platform':'iceberg','platform_instance':'commerce_polaris',
            'env':'DEV','namespace':'analytics','dataset_name':name,
            'expected_urn':f'urn:li:dataset:(urn:li:dataPlatform:iceberg,{name},DEV)',
            'status':'UNVERIFIED_EXPECTED','resolved_urn':None,
        })
    return {'contract':'commerce_dataset_identity_resolution','mode':'EXPECTED_ONLY','runtime_verified':False,'identities':items}

def main():
    """提供静态身份生成 CLI，并对真实 DataHub resolve 模式失败关闭。
    
    参数：--mode expected / resolve。
    输出：expected 模式写入 metadata/datahub/generated/dataset_identity_resolution.json。
    工程边界：resolve 需要真实 DataHub Runtime；当前源码入口显式 REFUSED，避免静态阶段伪造 Runtime identity。"""
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=['expected','resolve'],default='expected'); args=ap.parse_args()
    if args.mode=='resolve':
        raise SystemExit('REFUSED: real DataHub exact identity resolution remains DEFERRED in this working tree; use expected mode until DataHub Runtime is available.')
    payload=expected_payload(); out=ROOT/'metadata/datahub/generated/dataset_identity_resolution.json'; out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n'); print(out)
if __name__=='__main__': main()
