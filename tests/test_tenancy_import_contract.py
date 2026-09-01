"""Tenancy / Semantic Query 的循环导入回归测试。"""

import importlib
import sys


def _drop(prefix: str) -> None:
    """清理指定 package 的已加载模块，模拟全新 Python 进程导入顺序。"""

    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)


def test_tenancy_can_be_imported_before_semantic_query():
    """先导入 tenancy 再导入 semantic_query 时不能形成循环依赖。"""

    _drop("agent.tenancy")
    _drop("agent.semantic_query")

    tenancy = importlib.import_module("agent.tenancy")
    semantic_query = importlib.import_module("agent.semantic_query")

    assert tenancy.RequestContext is not None
    assert tenancy.GovernedRequestScopeEnforcer is not None
    assert semantic_query.MetricFlowSemanticQueryExecutor is not None


def test_semantic_query_can_be_imported_before_tenancy():
    """先导入 semantic_query 再导入 tenancy 也必须成功。"""

    _drop("agent.tenancy")
    _drop("agent.semantic_query")

    semantic_query = importlib.import_module("agent.semantic_query")
    tenancy = importlib.import_module("agent.tenancy")

    assert semantic_query.MetricFlowSemanticQueryExecutor is not None
    assert tenancy.GovernedRequestScopeEnforcer is not None
