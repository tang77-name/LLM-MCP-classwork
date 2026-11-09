# -*- coding: utf-8 -*-
"""上下文压缩：专用 Prompt、历史序列化、摘要构造与截断。

压缩请求的构造（**不携带任何工具 schema**，因此压缩期间模型无法发起
Function Calling，只能产出摘要文本）::

    [system] COMPACT_PROMPT（本文件的压缩任务提示词）
    [user]   序列化后的历史上下文（保留角色与顺序，含工具调用与工具结果）

生成的摘要末尾会追加一条归档说明，指明原始上下文的归档编号与
``recallContext`` 检索入口，形成“摘要 -> 归档 -> 检索”的闭环。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .tokens import estimate_tokens

#: 压缩目标约 5,000 tokens（Prompt 中已要求），超过该值 2 倍才强制截断
TARGET_SUMMARY_TOKENS = 5_000
MAX_SUMMARY_TOKENS = TARGET_SUMMARY_TOKENS * 2

#: 上下文压缩专用 Prompt（作业指定，勿随意改动语义）
COMPACT_PROMPT = (
    "你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，"
    "需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。\n\n"
    "请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，"
    "并生成一份约 5,000 Tokens 的 Markdown 摘要。\n\n"
    "摘要应重点保留以下内容：\n\n"
    "1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？\n"
    "2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？\n"
    "3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？"
    "是否存在尚未解决的问题？\n"
    "4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、"
    "执行命令、错误信息和必要的代码片段。\n"
    "5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，"
    "以及重要的设计取舍。\n"
    "6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。\n\n"
    "请遵守以下要求：\n\n"
    "- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。\n"
    "- 对已经验证的事实与尚未验证的推测作出明确区分。\n"
    "- 不要将计划完成的任务描述为已经完成。\n"
    "- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。\n"
    "- 对失败的尝试，只保留有助于避免重复错误的信息。\n"
    "- 如果信息存在冲突，应优先保留用户最新确认的要求，"
    "并简要说明仍然存在的冲突。\n"
    "- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，"
    "后续可以通过 `recallContext` 工具重新检索。\n"
    "- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。\n\n"
    "**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，"
    "就能够理解当前任务、继承关键决策，并继续开展工作。**"
)

#: 摘要末尾追加的归档说明
ARCHIVE_NOTE_TEMPLATE = (
    "\n\n---\n"
    "此前上下文已于 {time} 完成压缩，原始上下文保存在编号为 {archive_id} 的归档文件中。"
    "如需查找历史信息，请使用 recallContext 工具。"
)


def serialize_history(messages: List[Dict[str, Any]]) -> str:
    """把历史消息渲染成给压缩 LLM 阅读的纯文本（保留角色与顺序）。

    工具调用（参数）与工具返回结果都会写进去 —— 压缩输入必须是
    **完整原始上下文**，由模型负责取舍。
    """
    blocks: List[str] = []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            blocks.append(f"[{index}] {message}")
            continue
        role = str(message.get("role") or "?").upper()
        lines = [f"[{index}] {role}"]
        content = message.get("content")
        if content:
            lines.append(str(content))
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            lines.append(
                f"[调用工具] {function.get('name', '?')}"
                f"({function.get('arguments', '')})"
            )
        if role == "TOOL":
            lines.append(
                f"(tool_call_id={message.get('tool_call_id', '')}, "
                f"name={message.get('name', '')})"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_compact_messages(
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """构造压缩请求消息：压缩 Prompt 作为 system，待压缩历史作为 user。

    **不传 tools** —— 这是“压缩期间禁止执行其他 Function Calling”的保证。
    """
    body = (
        f"以下是待压缩的会话历史（共 {len(history)} 条消息，"
        f"含 System Prompt、对话与工具调用结果）：\n\n"
        + serialize_history(history)
    )
    return [
        {"role": "system", "content": COMPACT_PROMPT},
        {"role": "user", "content": body},
    ]


def cap_summary(text: str, max_tokens: int = MAX_SUMMARY_TOKENS) -> str:
    """把摘要控制在目标量级：超过上限时按估算比例截断到段落边界。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    tokens = estimate_tokens(cleaned)
    if tokens <= max_tokens:
        return cleaned
    keep_chars = int(len(cleaned) * (max_tokens / tokens) * 0.95)
    cut = cleaned[: max(keep_chars, 1)]
    boundary = cut.rfind("\n\n")
    if boundary > keep_chars // 2:  # 优先整段截断，避免留半句话
        cut = cut[:boundary]
    return (
        cut.rstrip()
        + "\n\n（摘要过长已截断；完整内容已保存到归档，可用 recallContext 检索。）"
    )


def build_summary(summary: str, archive_id: str, when: datetime) -> str:
    """摘要正文 + 归档说明（替换进会话的最终文本）。"""
    body = cap_summary(summary)
    note = ARCHIVE_NOTE_TEMPLATE.format(
        time=when.strftime("%Y-%m-%d %H:%M:%S"),
        archive_id=archive_id,
    )
    return body + note


__all__ = [
    "COMPACT_PROMPT",
    "ARCHIVE_NOTE_TEMPLATE",
    "TARGET_SUMMARY_TOKENS",
    "MAX_SUMMARY_TOKENS",
    "serialize_history",
    "build_compact_messages",
    "cap_summary",
    "build_summary",
]
