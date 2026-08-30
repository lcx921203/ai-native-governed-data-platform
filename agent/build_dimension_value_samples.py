from __future__ import annotations
import json
from pathlib import Path
from agent.dimension_values import GovernedDimensionValuePlanner, MetricFlowDimensionValueExecutor

def main():
    root=Path(__file__).resolve().parents[1]; planner=GovernedDimensionValuePlanner(root); executor=MetricFlowDimensionValueExecutor(root)
    cases={
      'region':planner.plan(metrics=['gross_sales'],dimension='store__region',question='gross_sales 有哪些地区可以筛？'),
      'brand':planner.plan(metrics=['gross_sales'],dimension='item__brand',question='gross_sales 有哪些品牌可以筛？'),
      'missing_metric':planner.plan(metrics=[],dimension='store__region',question='有哪些地区可以筛？'),
    }
    samples={name:{'plan':plan.to_dict(),'execution':executor.execute(plan).to_dict()} for name,plan in cases.items()}
    path=root/'agent/generated/dimension_value_samples.json'; path.write_text(json.dumps({'contract':'commerce_dimension_value_samples','samples':samples},ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
