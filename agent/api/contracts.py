"""Production Agent API（生产智能体接口）的 HTTP 契约。

API 只暴露用户真正需要的字段：
- question 作为输入；
- status / answer / answer_validated / trace_id 作为输出。

Router、Context、Tool Trace、内部 Warning 等调试信息不直接暴露给外部调用方，
避免把内部治理结构或运行配置当成公共 API Contract。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentQueryRequest(BaseModel):
    """Agent 查询请求；禁止额外字段并限制问题长度。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        """去除首尾空白；纯空白问题按非法请求处理。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class AgentQueryResponse(BaseModel):
    """外部 Agent API 的最小稳定响应。"""

    status: str
    answer: str
    answer_validated: bool
    trace_id: str


class HealthResponse(BaseModel):
    """Agent API 健康检查响应。"""

    status: str
