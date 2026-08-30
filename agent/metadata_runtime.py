"""Agent 元数据 Runtime Cutover 的证据门与 live DataHub 读取桥接。

只有 Phase 7 Evidence、exact identity 与环境 Gate 同时满足时才允许 live read；否则 Fail Closed。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RuntimeBinding:
    """一个 Dataset 已通过 Phase 7 Evidence 绑定到 live DataHub exact URN 的结果。

    ``collected_at`` 记录证据采集时间；对象只证明“允许 live read 的身份绑定”，
    不代表 Metric、Ownership 或 Lineage 内容本身已经正确。
    """

    dataset: str
    urn: str
    collected_at: str | None


@dataclass(frozen=True)
class RuntimeReadResult:
    """Agent 元数据 Runtime 读取的统一返回结果。

    ``available`` 表示 live read 是否真正可用；``payload`` 与 ``warning`` 分离，
    防止调用方把阻断 / 降级结果误当 live DataHub truth。
    """

    available: bool
    payload: dict[str, Any] | None = None
    warning: str | None = None


class DataHubMetadataRuntime:
    """Phase 7 Runtime Evidence 与 live DataHub read 之间的 Fail-closed Bridge。

    只有 evidence gate、metadata runtime 状态与 exact identity 三层同时通过，才允许调用 DataHub Adapter；
    否则返回受控 warning，不把 expected URN 冒充 resolved runtime identity。
    """

    def __init__(self, project_root: Path | str, *, adapter=None, evidence_dir=None):
        """初始化 DataHub Runtime cutover bridge。
        
        输入：项目根目录、可选 read adapter、可选 evidence_dir。
        行为：读取 metadata_runtime_cutover_policy.yml，默认证据目录指向 .runtime/evidence/phase7a/datahub。
        工程边界：是否允许 live read 由 Runtime evidence + env gate 共同决定。"""
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load((self.root / 'agent/contracts/metadata_runtime_cutover_policy.yml').read_text(encoding='utf-8'))
        self.evidence_dir = Path(evidence_dir) if evidence_dir else self.root / '.runtime/evidence/phase7a/datahub'
        self._adapter = adapter

    def binding(self, dataset: str) -> tuple[RuntimeBinding | None, str | None]:
        """为一个 governed dataset 建立经过证据验证的 exact Runtime binding。
        
        输入：canonical dataset model name。
        输出：成功返回 RuntimeBinding(dataset, exact urn, collected_at)；失败返回 warning，不猜绑定。
        证据要求：环境门打开、metadata plane evidence 与 identity evidence 都存在、runtime_verified=true、resolved_urn == expected_urn。
        工程边界：历史/静态证据不足以开启 live read；任何不一致都 fail closed。"""
        gate = self.policy['runtime']['allow_env']
        if os.getenv(gate, 'false').lower() != 'true':
            return None, f'DataHub Agent runtime read is disabled by {gate}.'
        metadata_path = self.evidence_dir / 'datahub_runtime.json'
        identity_path = self.evidence_dir / 'dataset_identity_resolution.json'
        if not metadata_path.exists() or not identity_path.exists():
            return None, 'Phase 7A DataHub runtime evidence is unavailable.'
        try:
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            identities = json.loads(identity_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None, 'Phase 7A DataHub runtime evidence is unreadable.'
        if metadata.get('runtime_verified') is not True or metadata.get('status') != self.policy['runtime']['required_metadata_status']:
            return None, 'DataHub metadata plane has not been runtime verified.'
        runtime_asset = next((x for x in metadata.get('assets', []) if x.get('model') == dataset), None)
        identity = next((x for x in identities.get('identities', []) if x.get('model') == dataset), None)
        if not runtime_asset or not identity or runtime_asset.get('status') != 'RUNTIME_VERIFIED':
            return None, f'{dataset} has no verified runtime identity.'
        observed = runtime_asset.get('identity') or {}
        status = observed.get('status')
        expected = observed.get('expected_urn')
        resolved = observed.get('resolved_urn')
        allowed = set(self.policy['runtime']['allowed_identity_statuses'])
        if status not in allowed or not resolved or resolved != expected:
            return None, f'{dataset} does not have an exact resolved identity.'
        if identity.get('resolved_urn') != resolved or identity.get('expected_urn') != resolved:
            return None, f'{dataset} identity evidence is inconsistent.'
        return RuntimeBinding(dataset, resolved, metadata.get('collected_at')), None
