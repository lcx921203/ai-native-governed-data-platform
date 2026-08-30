from __future__ import annotations
import json
from pathlib import Path
from agent.dimension_resolution import GovernedDimensionValueResolver

def main():
    root=Path(__file__).resolve().parents[1]; resolver=GovernedDimensionValueResolver(root)
    samples={
      'exact':resolver.resolve(metrics=['gross_sales'],raw_value='South',dimension_hint='store__region').to_dict(),
      'normalized':resolver.resolve(metrics=['gross_sales'],raw_value='coca cola',dimension_hint='item__brand').to_dict(),
      'fuzzy':resolver.resolve(metrics=['gross_sales'],raw_value='Coca Colaa',dimension_hint='item__brand').to_dict(),
      'not_found':resolver.resolve(metrics=['gross_sales'],raw_value='Pepsi',dimension_hint='item__brand').to_dict(),
    }
    path=root/'agent/generated/dimension_resolution_samples.json'; path.write_text(json.dumps({'contract':'commerce_dimension_resolution_samples','samples':samples},ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
