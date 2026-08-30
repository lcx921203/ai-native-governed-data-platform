"""Phase 3C 的结构化 Failure Classification（失败分类）契约。

失败语义必须由能够提供结构化证据的层来证明。Recovery 只消费已经证明的 FailureClass，
绝不从自由文本日志、异常字符串或 LLM 猜测中把 UNKNOWN 升级成“可安全自动重放”。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class FailureClass(str, Enum):
    """Recovery 使用的受治理失败类别。

    ``UNKNOWN`` 是明确的一等状态：证据不足时保持未知，并在自动化层 Fail Closed。
    """

    NONE = "none"
    TRANSIENT_RUNTIME = "transient_runtime"
    INFRASTRUCTURE_UNAVAILABLE = "infrastructure_unavailable"
    DETERMINISTIC_CODE = "deterministic_code"
    DATA_CONTRACT = "data_contract"
    UNKNOWN = "unknown"


FAILURE_CLASS_TAG = "commerce/failure_class"
FAILURE_CLASS_SOURCE_TAG = "commerce/failure_class_source"
FAILURE_COMPONENT_TAG = "commerce/failure_component"
FAILURE_REASON_TAG = "commerce/failure_reason"
FAILURE_STAGE_TAG = "commerce/failure_stage"


class FailureClassSource(str, Enum):
    """记录 FailureClass 是由哪类结构化证据来源证明的。

    Recovery 可以据此追溯“谁有资格作出这个分类”，而不是只拿一个无来源的标签。
    """

    EXECUTION_ADAPTER = "execution_adapter"
    DBT_ARTIFACT = "dbt_artifact"
    DBT_COMMAND = "dbt_command"
    EXPLICIT_APPLICATION = "explicit_application"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CommandFailureObservation:
    """命令执行适配器能够直接观察到的最小失败事实。

    输入包含命令是否存在、是否超时、依赖服务是否运行与退出码；不包含自由文本日志推断。
    """

    command_available: bool = True
    timed_out: bool = False
    service_running: bool = True
    return_code: int | None = None


@dataclass(frozen=True)
class DbtFailureObservation:
    """dbt 失败分类器的结构化输入。

    ``run_results`` 来自 dbt artifact；只有 dbt 自己能证明的 test / node 状态才参与分类。
    """

    command_name: str
    command_succeeded: bool
    run_results: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        """校验 dbt 失败观察对象的必要字段。

        输入：command_name、command_succeeded 与可选 run_results。
        工程边界：命令名为空时立即失败；Classifier 不能在缺少基本来源身份时猜测失败语义。
        """
        if not self.command_name:
            raise ValueError("command_name is required")


@dataclass(frozen=True)
class DbtFailureClassification:
    """dbt 分类器输出：失败类别 + 证据来源 + 稳定原因码。

    ``failed_test_ids`` 保留真正失败的数据测试 identity，便于后续排障而不依赖日志文本。
    """

    failure_class: FailureClass
    source: FailureClassSource
    reason_code: str
    failed_test_ids: tuple[str, ...] = ()


def classify_command_failure(observation: CommandFailureObservation) -> FailureClass:
    """把命令层运行事实归类成可治理 FailureClass。

    输入：命令是否可用、服务是否运行、是否超时、退出码等结构化事实。
    输出：FailureClass。
    工程边界：无法从这些事实证明确定根因时返回 UNKNOWN；后续 Recovery 必须 Fail Closed。
    """
    if not observation.command_available or not observation.service_running:
        return FailureClass.INFRASTRUCTURE_UNAVAILABLE
    if observation.timed_out:
        return FailureClass.TRANSIENT_RUNTIME
    return FailureClass.UNKNOWN


def _dbt_result_status(result: Mapping[str, Any]) -> str:
    """读取并规范化 dbt run_results 中单个节点的 status。

    输入：一个 dbt result Mapping。
    输出：去空格并转小写的状态字符串。
    """
    return str(result.get("status") or "").strip().lower()


def _dbt_unique_id(result: Mapping[str, Any]) -> str:
    """读取 dbt result 的稳定 unique_id。

    输入：一个 dbt result Mapping。
    输出：例如 ``test.project.test_name`` / ``model.project.model_name``。
    数据语义：unique_id 用来区分失败对象，不能只看进程退出码。
    """
    return str(result.get("unique_id") or "").strip()


def classify_dbt_failure(observation: DbtFailureObservation) -> DbtFailureClassification:
    """只根据 dbt 自己拥有的结构化证据分类失败。

    规则：test status=`fail` 可以证明 Data Contract 失败；``dbt parse`` 不连接 Warehouse，
    因此 parse 失败可以归为确定性 Project / Code 证据。``dbt compile`` 非零退出则不能直接
    证明是确定性代码错误，因为 compile 可能需要 Warehouse 连接与 introspective query。
    test/model 的 ``error`` 同样不足以证明业务语义原因。

    工程边界：message 与自由文本日志故意不参与分类；证据不足就返回 UNKNOWN。
    """

    command = observation.command_name.strip().lower()

    if observation.command_succeeded:
        return DbtFailureClassification(
            FailureClass.NONE,
            FailureClassSource.DBT_COMMAND,
            "dbt_command_succeeded",
        )

    if command == "parse":
        return DbtFailureClassification(
            FailureClass.DETERMINISTIC_CODE,
            FailureClassSource.DBT_COMMAND,
            "dbt_parse_failed",
        )

    if command == "compile" and observation.run_results is None:
        return DbtFailureClassification(
            FailureClass.UNKNOWN,
            FailureClassSource.DBT_COMMAND,
            "dbt_compile_failed_without_deterministic_evidence",
        )

    if observation.run_results is None:
        return DbtFailureClassification(
            FailureClass.UNKNOWN,
            FailureClassSource.DBT_COMMAND,
            "dbt_failed_without_run_results",
        )

    raw_results = observation.run_results.get("results", [])
    results = [r for r in raw_results if isinstance(r, Mapping)]

    failed_test_ids = tuple(
        _dbt_unique_id(result)
        for result in results
        if _dbt_unique_id(result).startswith("test.")
        and _dbt_result_status(result) == "fail"
    )
    if failed_test_ids:
        return DbtFailureClassification(
            FailureClass.DATA_CONTRACT,
            FailureClassSource.DBT_ARTIFACT,
            "dbt_data_test_failed",
            failed_test_ids=failed_test_ids,
        )

    return DbtFailureClassification(
        FailureClass.UNKNOWN,
        FailureClassSource.DBT_ARTIFACT,
        "dbt_nonzero_without_replay_safe_class",
    )


def failure_class_tags(
    failure_class: FailureClass,
    *,
    source: FailureClassSource,
    component: str,
    reason_code: str | None = None,
    stage: str | None = None,
) -> dict[str, str]:
    """把 FailureClass 投影成 Dagster 可查询的结构化标签。

    输入：failure_class、证据来源、组件，以及可选 reason/stage。
    输出：``dict[str, str]``，供 Run/Event/Recovery 读取。
    Dagster API：标签让 Sensor 和排障工具按结构化字段查询失败原因，而不是解析自由文本日志。
    工程边界：本函数只编码已证明的失败类别，不负责重新分类。
    """
    if not component:
        raise ValueError("component is required")
    tags = {
        FAILURE_CLASS_TAG: failure_class.value,
        FAILURE_CLASS_SOURCE_TAG: source.value,
        FAILURE_COMPONENT_TAG: component,
    }
    if reason_code:
        tags[FAILURE_REASON_TAG] = reason_code
    if stage:
        tags[FAILURE_STAGE_TAG] = stage
    return tags


def allow_step_retry(failure_class: FailureClass) -> bool:
    """只对已经证明“可安全重试”的 FailureClass 授予有界 Step Retry。

    当前仅 ``TRANSIENT_RUNTIME`` 与 ``INFRASTRUCTURE_UNAVAILABLE`` 返回 True；
    ``UNKNOWN``、Data Contract、Deterministic Code 一律不自动 Step Retry。
    """

    return failure_class in {
        FailureClass.TRANSIENT_RUNTIME,
        FailureClass.INFRASTRUCTURE_UNAVAILABLE,
    }
