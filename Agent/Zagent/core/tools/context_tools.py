# -*- coding: utf-8 -*-
"""``recallContext`` 工具：检索已归档的原始上下文。

与 :mod:`core.context.archive` 配合形成闭环::

    /compact 归档 -> LLM 摘要 -> 摘要缺少细节 -> recallContext 检索原文

检索为纯本地全文关键词匹配（无需向量库 / Embedding）：
空白分隔多个关键词，命中越多排名越靠前；返回片段带归档编号与消息位置，
并做长度截断，避免检索结果再次撑满上下文。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Optional

from ..context import ContextArchive
from .base import ToolRegistry, ToolSpec

#: 单次检索最多返回的命中条数
MATCH_LIMIT = 5
#: 检索结果回传给模型的最大字符数（超出截断）
MAX_RESULT_CHARS = 3000


def recall_context(
    text: str,
    id: Optional[str] = None,
    *,
    directory,
    limit: int = MATCH_LIMIT,
    max_chars: int = MAX_RESULT_CHARS,
) -> str:
    """执行一次归档检索（handler 实现，见工具 schema）。"""
    keyword = str(text or "").strip()
    if not keyword:
        raise ValueError("text 不能为空，请提供检索关键词")

    archive = ContextArchive(directory)
    matches = archive.search(
        keyword,
        archive_id=(str(id).strip() if id else None),
        limit=limit,
    )
    if not matches:
        scope = f"归档 {id}" if id else str(archive.directory)
        return f"未在 {scope} 中找到与“{keyword}”匹配的内容。"

    lines = [
        f"命中 {len(matches)} 条（关键词：{keyword}；"
        f"归档目录 {archive.directory}）："
    ]
    for match in matches:
        lines.append("")
        lines.append(
            f"[归档 {match.archive_id} · 第 {match.position} 条消息"
            f" · {match.role}]（命中 {match.score}/{len(keyword.split())} 个词）"
        )
        lines.append(match.snippet)

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = (
            result[:max_chars]
            + "\n…（结果过长已截断，可增加关键词或指定 id 缩小范围）"
        )
    return result


def register_recall_tool(
    registry: ToolRegistry,
    directory,
    *,
    limit: int = MATCH_LIMIT,
    max_chars: int = MAX_RESULT_CHARS,
) -> None:
    """把 ``recallContext`` 注册到已有工具注册表（已存在时跳过）。

    :param limit: 返回条数（config.ini ``[tools] recall_limit``）
    :param max_chars: 结果长度上限（config.ini ``[tools] recall_max_chars``）
    """
    if registry.get("recallContext") is not None:
        return

    registry.register(ToolSpec(
        name="recallContext",
        description=(
            "检索已被 /compact 压缩掉的原始上下文归档（.agents/context/*.jsonl）。"
            "当压缩摘要缺少早期对话的细节（文件路径、报错原文、决策过程、"
            "被放弃的方案等）时调用它找回原文。"
            "text 为必填检索关键词（可用空格分隔多个词，命中越多排名越靠前）；"
            "id 为可选的归档编号（如 20261022-153000，可在压缩提示或摘要末尾的"
            "归档说明中找到），省略时检索全部历史归档。"
            "返回命中的原始消息片段、归档编号与消息位置；"
            f"单次最多返回 {limit} 条，总长度不超过 {max_chars} 字符，"
            "拿到所需信息即可继续任务，不要反复全量检索。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "必填。要查找的关键词或文本内容，多个词用空格分隔。",
                },
                "id": {
                    "type": "string",
                    "description": (
                        "可选。归档编号（如 20261022-153000）；"
                        "省略时在全部历史归档中检索。"
                    ),
                },
            },
            "required": ["text"],
        },
        handler=partial(
            recall_context,
            directory=Path(directory),
            limit=limit,
            max_chars=max_chars,
        ),
    ))


__all__ = [
    "recall_context",
    "register_recall_tool",
    "MATCH_LIMIT",
    "MAX_RESULT_CHARS",
]
