"""Knowledge RAG 的 Embedding Provider 适配层。

当前真实 Provider 使用 OpenAI Embeddings API；静态测试可以注入 Fake Provider，
所以源码存在并不代表真实 OpenAI 网络调用已经执行。
"""

from __future__ import annotations

import os
from openai import OpenAI


class OpenAIKnowledgeEmbeddingProvider:
    """把文本批量转换为固定维度向量的 OpenAI Provider。"""

    def __init__(self, *, client: OpenAI | None = None, model: str | None = None, dimensions: int = 1536, batch_size: int = 64):
        """注入 OpenAI Client 或按环境创建默认 Client，并固定模型/维度/批大小。"""
        self.client = client or OpenAI()
        self.model = model or os.getenv('KNOWLEDGE_EMBEDDING_MODEL', 'text-embedding-3-small')
        self.dimensions = dimensions
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量调用 ``client.embeddings.create`` 并保持输入输出顺序一致。

        输入为空字符串会拒绝；API 返回后按 ``item.index`` 排序，最后再次校验向量条数，
        避免网络/API 异常造成文本与向量错位。真实调用需要 Runtime credential 与 gate。
        """
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            if any(not text.strip() for text in batch):
                raise ValueError('Embedding input cannot be empty.')
            response = self.client.embeddings.create(model=self.model, input=batch, dimensions=self.dimensions, encoding_format='float')
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
        if len(vectors) != len(texts):
            raise RuntimeError('Embedding result cardinality mismatch.')
        return vectors
