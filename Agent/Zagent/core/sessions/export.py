# -*- coding: utf-8 -*-
"""会话导出：把消息历史渲染为 Markdown 文本。

供 ``/export`` 命令（TUI / CLI）使用：导出文件默认写入
``.zagent/exports/session-<id>-<时间戳>.md``，便于复制、分享或归档。
纯函数实现：不读写文件、不依赖界面。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


def _fence(text: str, language: str = "text") -> str:
    """生成 Markdown 代码块（自动加长围栏，避免内容中的 ``` 破坏结构）。"""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text}\n{fence}"


def history_to_markdown(
    messages: Sequence[Dict[str, Any]],
    *,
    title: str = "",
    model: str = "",
    exported_at: Optional[datetime] = None,
) -> str:
    """把 ``messages`` 渲染为 Markdown 文档。

    :param messages: 会话历史（role / content / tool_calls ...）
    :param title: 会话标题（可选，写入文档头部）
    :param model: 模型名（可选，写入文档头部）
    :param exported_at: 导出时间（默认当前时间）
    """
    stamp = (exported_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = ["# Zagent 会话导出", ""]
    if title:
        lines.append(f"- 标题：{title}")
    lines.append(f"- 导出时间：{stamp}")
    if model:
        lines.append(f"- 模型：{model}")
    lines.append(f"- 消息数：{len(messages)}")
    lines.append("")

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip()
        content = str(message.get("content") or "").strip()
        tool_calls = message.get("tool_calls") or []

        if role == "system":
            lines += ["## System", "", content, ""]
        elif role == "user":
            lines += ["## 用户", "", content, ""]
        elif role == "assistant":
            lines += ["## Zagent", ""]
            if content:
                lines += [content, ""]
            for call in tool_calls:
                function = (call or {}).get("function") or {}
                name = function.get("name") or "?"
                arguments = function.get("arguments") or ""
                lines += [f"> 工具调用 `{name}`：`{arguments}`", ""]
        elif role == "tool":
            name = str(message.get("name") or "tool")
            lines += [f"### 工具结果 · {name}", "", _fence(content), ""]

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["history_to_markdown"]
