"""Model Context Card 的读取、Freshness 校验与 Raw Code fallback。

优先级：
    fresh prebuilt card
        >
    local deterministic build fallback（无 LLM）
        >
    explicit bounded raw-code snippet

关键边界：
- 源文件指纹不一致时，旧 Card 立即 STALE，默认 Fail Closed；
- Raw Code 不会默认整文件进入 Context；
- Code Context 只能解释“数据实际上怎么加工”，不能覆盖 MetricFlow 指标权威。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import yaml

from .builder import ModelContextCardBuilder, git_blob_sha
from .contracts import (
    ModelContextCard,
    ModelContextResolution,
    ModelContextStatus,
    RawCodeSnippet,
)


class GovernedModelContextRepository:
    """受治理的 Code Context Repository。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/model_context_policy.yml").read_text(encoding="utf-8")
        )
        self.builder = ModelContextCardBuilder(self.root)
        self.prebuilt_root = self.root / self.policy["paths"]["prebuilt_root"]

    def resolve(
        self,
        model: str,
        *,
        allow_local_build_fallback: bool | None = None,
    ) -> ModelContextResolution:
        """解析一张 Fresh Card。

        Production 建议 CI 预构建 Card；
        本地 fallback 只是确定性 parser，不调用 LLM，因此不会产生模型 token 成本。
        """

        allow_fallback = (
            bool(self.policy["fallback"]["allow_local_build_when_prebuilt_missing"])
            if allow_local_build_fallback is None
            else bool(allow_local_build_fallback)
        )
        path = self.prebuilt_root / f"{model}.yml"

        if path.exists():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                card = ModelContextCard.from_dict(raw)
            except Exception as exc:  # 读取损坏的受治理产物时明确失败
                return ModelContextResolution(
                    ModelContextStatus.ERROR,
                    model,
                    warnings=(f"Prebuilt Model Context Card could not be parsed: {exc}",),
                )

            stale = self._stale_sources(card)
            if stale:
                return ModelContextResolution(
                    ModelContextStatus.STALE,
                    model,
                    evidence_mode="PREBUILT_CARD_STALE",
                    warnings=(
                        "Model Context Card source fingerprint mismatch; rebuild the card before use.",
                        *tuple(stale),
                    ),
                )

            warning = self._budget_warning(card)
            if warning:
                return ModelContextResolution(
                    ModelContextStatus.BLOCKED,
                    model,
                    evidence_mode="PREBUILT_CARD",
                    warnings=(warning,),
                )

            return ModelContextResolution(
                ModelContextStatus.RESOLVED,
                model,
                card=card,
                evidence_mode="PREBUILT_CARD",
                estimated_tokens=self.builder.estimate_tokens(card),
            )

        if not allow_fallback:
            return ModelContextResolution(
                ModelContextStatus.NOT_FOUND,
                model,
                warnings=("No prebuilt Model Context Card exists and local build fallback is disabled.",),
            )

        card = self.builder.build(model)
        if card is None:
            return ModelContextResolution(
                ModelContextStatus.NOT_FOUND,
                model,
                warnings=("No unique dbt SQL model was found for Model Context.",),
            )

        warning = self._budget_warning(card)
        if warning:
            return ModelContextResolution(
                ModelContextStatus.BLOCKED,
                model,
                evidence_mode="LOCAL_CODE_DERIVED",
                warnings=(warning,),
            )

        return ModelContextResolution(
            ModelContextStatus.RESOLVED,
            model,
            card=card,
            evidence_mode="LOCAL_CODE_DERIVED",
            estimated_tokens=self.builder.estimate_tokens(card),
            warnings=(
                "No prebuilt card was found; context was derived locally without an LLM. "
                "Production should prebuild cards in CI.",
            ),
        )

    def write_prebuilt(self, models: Iterable[str] | None = None) -> tuple[Path, ...]:
        """离线生成受治理 Card 文件，供 CI / 构建阶段使用。"""

        self.prebuilt_root.mkdir(parents=True, exist_ok=True)
        if models is None:
            cards = self.builder.build_all()
        else:
            cards = tuple(
                card
                for model in models
                for card in [self.builder.build(model)]
                if card is not None
            )

        written: list[Path] = []
        for card in cards:
            path = self.prebuilt_root / f"{card.model}.yml"
            path.write_text(
                yaml.safe_dump(
                    card.to_dict(),
                    allow_unicode=True,
                    sort_keys=False,
                    width=120,
                ),
                encoding="utf-8",
            )
            written.append(path)
        return tuple(written)

    def raw_snippet(
        self,
        model: str,
        *,
        start_line: int,
        end_line: int,
        allow_raw_fallback: bool = False,
    ) -> RawCodeSnippet:
        """按显式行号读取有限 Raw Code；默认关闭。"""

        if not allow_raw_fallback:
            return RawCodeSnippet(
                ModelContextStatus.BLOCKED,
                model,
                warnings=(
                    "Raw code fallback is disabled by default; use Model Context Card first.",
                ),
            )

        path = self.builder.model_sql_path(model)
        if path is None:
            return RawCodeSnippet(
                ModelContextStatus.NOT_FOUND,
                model,
                warnings=("No unique dbt SQL model was found.",),
            )

        if start_line < 1 or end_line < start_line:
            return RawCodeSnippet(
                ModelContextStatus.BLOCKED,
                model,
                warnings=("Raw code line range is invalid.",),
            )

        limits = self.policy["limits"]
        requested_lines = end_line - start_line + 1
        max_lines = int(limits["max_raw_snippet_lines"])
        if requested_lines > max_lines:
            return RawCodeSnippet(
                ModelContextStatus.BLOCKED,
                model,
                source_path=path.relative_to(self.root).as_posix(),
                warnings=(f"Raw code snippet requested {requested_lines} lines; maximum is {max_lines}.",),
            )

        lines = path.read_text(encoding="utf-8").splitlines()
        if start_line > len(lines):
            return RawCodeSnippet(
                ModelContextStatus.NOT_FOUND,
                model,
                source_path=path.relative_to(self.root).as_posix(),
                warnings=("Raw code start line is outside the source file.",),
            )

        actual_end = min(end_line, len(lines))
        content = "\n".join(lines[start_line - 1:actual_end])
        max_chars = int(limits["max_raw_snippet_chars"])
        if len(content) > max_chars:
            return RawCodeSnippet(
                ModelContextStatus.BLOCKED,
                model,
                source_path=path.relative_to(self.root).as_posix(),
                start_line=start_line,
                end_line=actual_end,
                warnings=(f"Raw code snippet exceeds {max_chars} characters.",),
            )

        chars_per_token = max(1, int(limits["token_estimate_chars_per_token"]))
        estimate = (len(content) + chars_per_token - 1) // chars_per_token
        return RawCodeSnippet(
            ModelContextStatus.RESOLVED,
            model,
            source_path=path.relative_to(self.root).as_posix(),
            start_line=start_line,
            end_line=actual_end,
            content=content,
            estimated_tokens=estimate,
        )

    def _stale_sources(self, card: ModelContextCard) -> list[str]:
        """Card 中任一依赖源文件变化，都视为 STALE。"""

        stale: list[str] = []
        for item in card.source_fingerprints:
            path = self.root / item.path
            if not path.exists():
                stale.append(f"Missing source file: {item.path}")
                continue
            current = git_blob_sha(path.read_text(encoding="utf-8"))
            if current != item.git_blob_sha:
                stale.append(f"Changed source file: {item.path}")
        return stale

    def _budget_warning(self, card: ModelContextCard) -> str | None:
        """在进入 LLM 前先做 Context 大小预算。"""

        payload = json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True)
        max_chars = int(self.policy["limits"]["max_card_chars"])
        if len(payload) > max_chars:
            return (
                f"Model Context Card is {len(payload)} characters; governed maximum is {max_chars}. "
                "Tighten the card extractor instead of sending a larger context."
            )
        return None
