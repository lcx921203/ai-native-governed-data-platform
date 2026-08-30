from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SemanticRuntimeReadiness:
    ready: bool
    evidence: str
    reason: str | None = None


class SemanticRuntimeGuard:
    """Uniform Phase 7 eligibility gate for real MetricFlow execution."""

    def __init__(self, project_root: Path | str, *, runner=None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load((self.root / 'agent/contracts/semantic_runtime_cutover_policy.yml').read_text(encoding='utf-8'))
        self.semantic_policy = yaml.safe_load((self.root / 'agent/contracts/semantic_query_policy.yml').read_text(encoding='utf-8'))
        self.runner = runner or subprocess.run
        self._cached: tuple[float, SemanticRuntimeReadiness] | None = None

    def check(self) -> SemanticRuntimeReadiness:
        gate = self.policy['runtime']['allow_env']
        if os.getenv(gate, 'false').lower() != 'true':
            return SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', f'Agent semantic runtime is disabled by {gate}.')
        path = self.root / self.policy['runtime']['core_evidence']
        if not path.exists():
            return SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', 'Phase 7A core runtime evidence does not exist.')
        try:
            core = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', 'Phase 7A core runtime evidence is unreadable.')
        if core.get('runtime_verified') is not True or core.get('status') != self.policy['runtime']['required_core_status']:
            return SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', 'Phase 7A core runtime is not verified.')
        for component in self.policy['runtime']['required_core_evidence']:
            if (core.get('evidence') or {}).get(component, {}).get('status') != 'PASS':
                return SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', f'Required component {component} is not PASS.')
        ttl = int(self.policy['runtime']['health_cache_seconds'])
        if self._cached and time.monotonic() - self._cached[0] <= ttl:
            return self._cached[1]
        configured = os.getenv(self.semantic_policy['runtime']['metricflow_bin_env'], '').strip()
        mf = Path(configured).expanduser().resolve() if configured else (self.root / self.semantic_policy['runtime']['default_metricflow_bin']).resolve()
        if not mf.exists():
            result = SemanticRuntimeReadiness(False, 'STATIC_CONTRACT', f'MetricFlow CLI does not exist at {mf}.')
            self._cached = (time.monotonic(), result)
            return result
        project_dir = self.root / self.semantic_policy['runtime']['project_dir']
        env = os.environ.copy(); env['DBT_PROFILES_DIR'] = str(project_dir)
        proc = self.runner([str(mf), *self.policy['runtime']['metricflow_health_command']], cwd=str(project_dir), env=env, text=True, capture_output=True, check=False)
        result = SemanticRuntimeReadiness(proc.returncode == 0, 'RUNTIME_VERIFIED' if proc.returncode == 0 else 'STATIC_CONTRACT', None if proc.returncode == 0 else 'MetricFlow current health check failed.')
        self._cached = (time.monotonic(), result)
        return result
