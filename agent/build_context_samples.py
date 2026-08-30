from __future__ import annotations
import json
from pathlib import Path
from agent.tools import GovernedMetadataTools

def main():
    root=Path(__file__).resolve().parents[1]; tools=GovernedMetadataTools(root)
    samples={
      'search':tools.search_metadata(query='activity net sales',limit=10),
      'metric':tools.get_metric_context(metric='activity_net_sales'),
      'entity':tools.get_entity_context(entity='order'),
      'dataset':tools.get_dataset_context(dataset='orders'),
      'lineage':tools.get_lineage_context(dataset='orders',direction='upstream',max_hops=2),
      'runtime':tools.get_runtime_context(dataset='orders'),
    }
    out={'contract':'commerce_agent_context_samples','samples':samples}
    path=root/'agent/generated/context_samples.json'; path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(path)
if __name__=='__main__': main()
