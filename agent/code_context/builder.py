"""Code -> Model Context Card 的确定性构建器。

这里不调用 LLM。它只做：
1. 读取 dbt SQL；
2. 提取 config / ref / source / execution-window / join / predicate 等代码事实；
3. 从 dbt schema YAML 读取 description / grain；
4. 从 Semantic YAML 读取 Business Time / Entity / Dimension / Metric；
5. 生成带源文件 Git-blob 指纹的小型 Card。

注意：
- SQL 解析采用“只提取高置信、有限事实”的策略；
- 无法可靠解析的复杂逻辑不会被本模块自行解释；
- 需要进一步解释时，后续只能显式请求 bounded raw-code snippet（有限源码片段）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .contracts import ModelContextCard, SourceFingerprint


_REF_RE = re.compile(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)")
_SOURCE_RE = re.compile(
    r"source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
)
_WINDOW_RE = re.compile(r"shopify_window_predicate\(\s*['\"]([^'\"]+)['\"]\s*\)")
_MACRO_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_CONFIG_BLOCK_RE = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
_CONFIG_ITEM_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(\[[^\]]*\]|'[^']*'|\"[^\"]*\"|true|false|True|False|-?\d+(?:\.\d+)?)",
    re.S,
)


def git_blob_sha(text: str) -> str:
    """按 Git blob object 规则计算 SHA-1，无需调用 git 命令。"""

    payload = text.encode("utf-8")
    header = f"blob {len(payload)}\0".encode("utf-8")
    return hashlib.sha1(header + payload).hexdigest()


class ModelContextCardBuilder:
    """从当前仓库代码构建紧凑 Model Context Card。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/model_context_policy.yml").read_text(encoding="utf-8")
        )
        paths = self.policy["paths"]
        self.models_root = self.root / paths["dbt_models_root"]
        self.schema_path = self.root / paths["marts_schema"]
        self.semantic_path = self.root / paths["semantic_contract"]

    def model_sql_path(self, model: str) -> Path | None:
        """要求模型 SQL 在 dbt models 下唯一，避免猜路径。"""

        matches = list(self.models_root.rglob(f"{model}.sql"))
        return matches[0] if len(matches) == 1 else None

    def build(self, model: str) -> ModelContextCard | None:
        """构建一张 Card；找不到唯一 SQL 时返回 None。"""

        sql_path = self.model_sql_path(model)
        if sql_path is None:
            return None

        sql_text = sql_path.read_text(encoding="utf-8")
        schema_text = self.schema_path.read_text(encoding="utf-8") if self.schema_path.exists() else ""
        semantic_text = self.semantic_path.read_text(encoding="utf-8") if self.semantic_path.exists() else ""

        schema_item = self._model_yaml_item(schema_text, model)
        semantic_item = self._model_yaml_item(semantic_text, model)

        description = str((schema_item or {}).get("description") or "").strip()
        grain = self._grain(description)

        semantic = self._semantic_facts(semantic_item or {})
        limits = self.policy["limits"]

        refs = tuple(dict.fromkeys(_REF_RE.findall(sql_text)))[: int(limits["max_upstream_refs"])]
        sources = tuple(
            dict.fromkeys(f"{source}.{table}" for source, table in _SOURCE_RE.findall(sql_text))
        )[: int(limits["max_upstream_sources"])]
        windows = tuple(dict.fromkeys(_WINDOW_RE.findall(sql_text)))[: int(limits["max_execution_window_fields"])]

        macros = tuple(
            name
            for name in dict.fromkeys(_MACRO_RE.findall(sql_text))
            if name not in {"config", "ref", "source"}
        )[: int(limits["max_macros"])]

        card = ModelContextCard(
            version=int(self.policy["version"]),
            model=model,
            description=description,
            grain=grain,
            source_sql=sql_path.relative_to(self.root).as_posix(),
            source_fingerprints=tuple(
                item
                for item in (
                    self._fingerprint(sql_path, "EXECUTABLE_CODE"),
                    self._fingerprint(self.schema_path, "DBT_SCHEMA_CONTRACT") if self.schema_path.exists() else None,
                    self._fingerprint(self.semantic_path, "DBT_METRICFLOW_SEMANTICS") if self.semantic_path.exists() else None,
                )
                if item is not None
            ),
            config=self._config(sql_text),
            upstream_refs=refs,
            upstream_sources=sources,
            execution_window_fields=windows,
            macros=macros,
            join_signals=self._signals(
                sql_text,
                kind="join",
                limit=int(limits["max_join_signals"]),
            ),
            predicate_signals=self._signals(
                sql_text,
                kind="predicate",
                limit=int(limits["max_predicate_signals"]),
            ),
            business_time=semantic["business_time"],
            entities=semantic["entities"],
            dimensions=semantic["dimensions"],
            metrics=semantic["metrics"],
        )

        fingerprint = self._card_fingerprint(card)
        return replace(card, card_fingerprint=fingerprint)

    def build_all(self) -> tuple[ModelContextCard, ...]:
        """构建 dbt models 下全部唯一 SQL 模型的 Card。"""

        cards: list[ModelContextCard] = []
        for path in sorted(self.models_root.rglob("*.sql")):
            card = self.build(path.stem)
            if card is not None:
                cards.append(card)
        # 同名模型会被 build() 的唯一性保护排除；这里再按 model 去重。
        unique: dict[str, ModelContextCard] = {card.model: card for card in cards}
        return tuple(unique[name] for name in sorted(unique))

    def estimate_tokens(self, card: ModelContextCard) -> int:
        """只做成本预算，不声称等于具体模型 tokenizer 的精确 token 数。"""

        chars = len(
            json.dumps(
                card.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        chars_per_token = max(1, int(self.policy["limits"]["token_estimate_chars_per_token"]))
        return (chars + chars_per_token - 1) // chars_per_token

    def _fingerprint(self, path: Path, authority: str) -> SourceFingerprint:
        text = path.read_text(encoding="utf-8")
        return SourceFingerprint(
            path=path.relative_to(self.root).as_posix(),
            git_blob_sha=git_blob_sha(text),
            authority=authority,
        )

    @staticmethod
    def _model_yaml_item(text: str, model: str) -> dict[str, Any] | None:
        if not text.strip():
            return None
        raw = yaml.safe_load(text) or {}
        for item in raw.get("models", ()) or ():
            if str(item.get("name")) == model:
                return dict(item)
        return None

    @staticmethod
    def _grain(description: str) -> str | None:
        """只从明确写出“一行”的 schema 描述提取 Grain，不自行推断。"""

        if not description:
            return None
        compact = " ".join(description.split())
        for sentence in re.split(r"[。；;]", compact):
            sentence = sentence.strip()
            if "一行" in sentence:
                return sentence
        return None

    @staticmethod
    def _config(sql_text: str) -> dict[str, Any]:
        match = _CONFIG_BLOCK_RE.search(sql_text)
        if not match:
            return {}
        config: dict[str, Any] = {}
        for key, raw_value in _CONFIG_ITEM_RE.findall(match.group(1)):
            try:
                config[key] = ast.literal_eval(raw_value)
            except (ValueError, SyntaxError):
                lowered = raw_value.lower()
                if lowered in {"true", "false"}:
                    config[key] = lowered == "true"
                else:
                    config[key] = raw_value.strip()
        return config

    def _semantic_facts(self, model: dict[str, Any]) -> dict[str, Any]:
        limits = self.policy["limits"]
        entities: list[dict[str, Any]] = []
        dimensions: list[dict[str, Any]] = []

        for column in model.get("columns", ()) or ():
            name = str(column.get("name", ""))
            entity = column.get("entity")
            if entity:
                entities.append(
                    {
                        "column": name,
                        "name": str(entity.get("name", "")),
                        "type": str(entity.get("type", "")),
                    }
                )
            dimension = column.get("dimension")
            if dimension:
                dimensions.append(
                    {
                        "name": name,
                        "type": str(dimension.get("type", "")),
                        "granularity": column.get("granularity"),
                    }
                )

        metrics: list[dict[str, Any]] = []
        for metric in model.get("metrics", ()) or ():
            metrics.append(
                {
                    "name": str(metric.get("name", "")),
                    "type": str(metric.get("type", "")),
                    "agg": metric.get("agg"),
                    "expr": metric.get("expr"),
                    "agg_time_dimension": metric.get("agg_time_dimension"),
                }
            )

        return {
            "business_time": model.get("agg_time_dimension"),
            "entities": tuple(entities[: int(limits["max_entities"])]),
            "dimensions": tuple(dimensions[: int(limits["max_dimensions"])]),
            "metrics": tuple(metrics[: int(limits["max_metrics"])]),
        }

    @staticmethod
    def _normalize_signal(lines: list[str]) -> str:
        return " ".join(" ".join(line.strip().split()) for line in lines if line.strip())

    def _signals(self, sql_text: str, *, kind: str, limit: int) -> tuple[str, ...]:
        """抽取有限 SQL 片段作为“定位线索”，不把它们提升为完整业务解释。"""

        lines = sql_text.splitlines()
        signals: list[str] = []

        for index, raw in enumerate(lines):
            stripped = raw.strip()
            low = stripped.lower()
            if not stripped or stripped.startswith("--") or stripped.startswith("{#"):
                continue

            if kind == "join":
                matched = re.match(r"(?:(?:inner|left|right|full|cross)\s+)?join\s+", low)
                if not matched:
                    continue
                chunk = [stripped]
                # ON / AND 往往在后续 1~2 行；只取很短的局部证据。
                for extra in lines[index + 1:index + 3]:
                    extra_s = extra.strip()
                    if extra_s.lower().startswith(("on ", "and ")):
                        chunk.append(extra_s)
                signals.append(self._normalize_signal(chunk))

            elif kind == "predicate":
                # 只记录 where / when 以及紧随其后的有限 and 条件。
                if low.startswith("where "):
                    chunk = [stripped]
                    for extra in lines[index + 1:index + 3]:
                        extra_s = extra.strip()
                        if extra_s.lower().startswith("and "):
                            chunk.append(extra_s)
                    signals.append(self._normalize_signal(chunk))
                elif low.startswith("when "):
                    chunk = [stripped]
                    for extra in lines[index + 1:index + 3]:
                        extra_s = extra.strip()
                        if extra_s.lower().startswith("and "):
                            chunk.append(extra_s)
                    signals.append(self._normalize_signal(chunk))

            if len(signals) >= limit:
                break

        return tuple(dict.fromkeys(signals))

    @staticmethod
    def _card_fingerprint(card: ModelContextCard) -> str:
        raw = card.to_dict()
        raw["card_fingerprint"] = ""
        payload = json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
