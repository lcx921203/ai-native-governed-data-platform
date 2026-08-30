"""MCP Deployment Profile（部署角色）到可见 Tool 集合的映射。

Profile 控制“能看见哪些能力”，OAuth Scope 控制“这次调用有没有权限”，
Governed Registry 最后控制“是否允许真正执行”。三层都通过才会 dispatch。
"""

PROFILES = {
    'knowledge_only': {'search_knowledge', 'fetch_knowledge'},
    'analyst': {
        'get_dataset_context', 'get_lineage_context',
        'get_metric_context', 'query_semantic_metric', 'query_semantic_metrics',
        'get_dimension_values', 'resolve_dimension_value',
        'search_knowledge', 'fetch_knowledge',
    },
    'operator_read': {
        'get_dataset_context', 'get_lineage_context',
        'get_metric_context', 'query_semantic_metric', 'query_semantic_metrics',
        'get_dimension_values', 'resolve_dimension_value',
        'get_runtime_context',
        'search_knowledge', 'fetch_knowledge',
    },
}
