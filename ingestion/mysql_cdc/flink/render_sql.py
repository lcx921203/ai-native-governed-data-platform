"""把 MySQL CDC SQL 模板中的 ${ENV_NAME} 替换成部署环境变量。

为什么不把密码直接写进 SQL：
- SQL 模板要进 Git；真实账号密码不能进源码仓库。
- 部署系统 / Secret Manager 应负责把密钥注入环境变量。

Python 学习点：
- ``re.sub`` 可以用函数作为 replacement；每匹配到一个 ${NAME} 都调用一次函数。
- ``os.environ[name]`` 在变量不存在时会抛 KeyError，这里故意让配置缺失尽早失败。
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def render(template: str) -> str:
    """输入模板字符串，输出已经替换环境变量的 SQL。"""

    def replace(match: re.Match[str]) -> str:
        """把 SQL 模板中的一个 ``${ENV_NAME}`` 占位符替换成环境变量值。
        
        输入：正则匹配对象。
        输出：对应环境变量的字符串值。
        工程边界：必需变量缺失时立即失败（Fail Closed），避免生成带空密码/空地址的“看似可执行”SQL。
        """
        name = match.group(1)
        try:
            value = os.environ[name]
        except KeyError as exc:
            raise RuntimeError(f"缺少运行环境变量: {name}") from exc

        # SQL 单引号用两个单引号转义，避免环境变量中的 ' 破坏字符串字面量。
        return value.replace("'", "''")

    return PLACEHOLDER.sub(replace, template)


def main() -> None:
    """读取 Flink CDC SQL 模板、完成环境变量渲染并输出可执行 SQL。
    
    输入：模板文件与运行环境变量。
    输出：渲染后的 Flink SQL 文本。
    工程边界：本脚本只做配置注入，不启动 Flink Job；真实 CDC / Checkpoint / Iceberg 结果属于 Runtime 证据。
    """
    parser = argparse.ArgumentParser(description="渲染 MySQL CDC Flink SQL")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rendered = render(args.template.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
