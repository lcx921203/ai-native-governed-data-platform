"""Knowledge Retrieval 的离线/Runtime 评估指标。

这里计算 MRR、Recall、NDCG 与延迟分位数；这些函数只负责数学比较，
真正的 Runtime Verified 需要 live runner 产生 evidence。
"""

from __future__ import annotations

import math
from statistics import median
from typing import Iterable


def reciprocal_rank(result_document_ids: list[str], relevant: set[str], *, k: int) -> float:
    """计算 MRR 单 query 的 Reciprocal Rank：首个相关文档排名的倒数。"""
    for rank, document_id in enumerate(result_document_ids[:k], start=1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def recall_at_k(result_document_ids: list[str], relevant: set[str], *, k: int) -> float:
    """计算前 k 个结果覆盖了多少标注相关文档。"""
    if not relevant:
        return 1.0
    return len(set(result_document_ids[:k]) & relevant) / len(relevant)


def ndcg_at_k(result_document_ids: list[str], relevance: dict[str, int], *, k: int) -> float:
    """计算带分级相关性的 NDCG@k，奖励高相关文档排在更前面。"""

    def dcg(values: list[int]) -> float:
        """把一组 relevance grade 按对数位置折损累计成 DCG。"""
        return sum((2**rel - 1) / math.log2(rank + 1) for rank, rel in enumerate(values, start=1))
    observed = [int(relevance.get(doc, 0)) for doc in result_document_ids[:k]]
    ideal = sorted((int(v) for v in relevance.values()), reverse=True)[:k]
    denom = dcg(ideal)
    return dcg(observed) / denom if denom else 1.0


def percentile(values: list[float], p: float) -> float:
    """用 nearest-rank 方式计算延迟等观测值的分位数。"""
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, math.ceil(p * len(values)) - 1))
    return values[index]
