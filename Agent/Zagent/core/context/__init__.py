# -*- coding: utf-8 -*-
"""上下文管理：Token 估算与监控、原始上下文归档、压缩任务提示词。

结构（刻意保持小而直白，便于理解与维护）：

- :mod:`core.context.tokens`   Token 估算 + 上下文占用监控（80% 阈值）
- :mod:`core.context.archive`  ``.agents/context/*.jsonl`` 归档写入与关键词检索
- :mod:`core.context.compaction` 压缩专用 Prompt、历史序列化、摘要构造

依赖方向：``core.context -> 标准库``；不 import 任何前端 / LLM 客户端。
"""

from .archive import ContextArchive, ArchiveMatch
from .compaction import (
    ARCHIVE_NOTE_TEMPLATE,
    COMPACT_PROMPT,
    build_compact_messages,
    build_summary,
    cap_summary,
    serialize_history,
)
from .tokens import (
    DEFAULT_CONTEXT_WINDOW,
    WARN_RATIO,
    ContextMonitor,
    ContextUsage,
    estimate_messages,
    estimate_tokens,
)

__all__ = [
    "ContextArchive",
    "ArchiveMatch",
    "COMPACT_PROMPT",
    "ARCHIVE_NOTE_TEMPLATE",
    "build_compact_messages",
    "build_summary",
    "cap_summary",
    "serialize_history",
    "DEFAULT_CONTEXT_WINDOW",
    "WARN_RATIO",
    "ContextMonitor",
    "ContextUsage",
    "estimate_messages",
    "estimate_tokens",
]
