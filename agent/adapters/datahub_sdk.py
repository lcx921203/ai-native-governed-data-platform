"""Agent 访问 DataHub 的受治理只读 SDK Adapter。

模块只暴露 exact Dataset / bounded lineage 读取能力，不提供 mutation 或通用 Graph Query。
"""

from __future__ import annotations

from typing import Any


class ExactDataHubReadAdapter:
    """DataHub 的轻量只读 Adapter。

    只暴露 Agent 真正需要的 exact entity / bounded lineage 读取，不暴露 mutation 或任意 Graph Query。
    这样 DataHub 仍是 Metadata Authority，而 Agent 只能通过受治理窄接口消费它。
    """

    def __init__(self, client: Any):
        """保存由上层注入的 DataHub SDK Client。
        
        工程边界：Adapter 本身不负责认证策略，也不创建 mutation-capable 通用工具面；它只包住后面两个精确只读方法。"""
        self.client = client

    def get_dataset(self, exact_urn: str) -> Any:
        """按 exact Dataset URN 读取一个 DataHub Dataset。
        
        输入：必须以 urn:li:dataset: 开头的精确 URN。
        DataHub API：client.entities.get(exact_urn)。
        输出：SDK Dataset entity。
        工程边界：禁止模糊名称搜索，避免 Agent 把相似名字绑定成错误资产。"""
        if not exact_urn.startswith("urn:li:dataset:"):
            raise ValueError("exact Dataset URN required")
        return self.client.entities.get(exact_urn)

    def get_lineage(self, exact_urn: str, *, direction: str, max_hops: int = 2) -> Any:
        """读取一个 exact Dataset URN 的有界 DataHub lineage。
        
        输入：exact URN、upstream/downstream、max_hops 1–2。
        DataHub API：client.lineage.get_lineage()。
        输出：受限 hop 数的 lineage 结果。
        工程边界：方向和 hop 超界直接拒绝；Agent 不获得任意 Graph 查询能力。"""
        if not exact_urn.startswith("urn:li:dataset:"):
            raise ValueError("exact Dataset URN required")
        if direction not in {"upstream", "downstream"}:
            raise ValueError("direction must be upstream or downstream")
        if not 1 <= max_hops <= 2:
            raise ValueError("max_hops must be between 1 and 2")
        return self.client.lineage.get_lineage(
            source_urn=exact_urn,
            direction=direction,
            max_hops=max_hops,
        )
