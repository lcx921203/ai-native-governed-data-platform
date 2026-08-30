"""MCP OAuth Scope（权限范围）与 Tool 的最小权限映射。

每次 Tool Invocation 同时要求基础只读 Scope 和具体能力 Scope；
模型本身不能通过 Prompt 或参数绕过这个映射。
"""

MCP_BASE_READ = 'commerce:mcp:read'
METADATA_READ = 'commerce:metadata:read'
SEMANTIC_READ = 'commerce:semantic:read'
OPERATIONS_READ = 'commerce:operations:read'
KNOWLEDGE_READ = 'commerce:knowledge:read'

TOOL_REQUIRED_SCOPE = {
    'get_dataset_context': METADATA_READ,
    'get_lineage_context': METADATA_READ,
    'get_metric_context': SEMANTIC_READ,
    'query_semantic_metric': SEMANTIC_READ,
    'query_semantic_metrics': SEMANTIC_READ,
    'get_dimension_values': SEMANTIC_READ,
    'resolve_dimension_value': SEMANTIC_READ,
    'get_runtime_context': OPERATIONS_READ,
    'search_knowledge': KNOWLEDGE_READ,
    'fetch_knowledge': KNOWLEDGE_READ,
}
