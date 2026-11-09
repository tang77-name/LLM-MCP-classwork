# -*- coding: utf-8 -*-
"""消息与流式结果的数据模型（OpenAI 协议相关的纯数据结构）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict

#: 与 OpenAI Chat Completions 兼容的消息字典
ChatMessage = Dict[str, Any]


class ToolCall(TypedDict):
    """一条完整的工具调用（SSE 增量已拼接完毕）。"""

    id: str
    name: str
    arguments: str  # 原始 JSON 字符串


@dataclass(frozen=True)
class StreamResult:
    """一次流式响应消费完毕后的汇总结果。"""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    #: API 报告的用量（``usage.prompt_tokens`` 等）；服务不返回时为 None
    usage: Optional[Dict[str, Any]] = None


def assistant_message(result: StreamResult) -> ChatMessage:
    """把流式汇总结果还原为一条 assistant 消息。"""
    msg: ChatMessage = {"role": "assistant", "content": result.content or None}
    if result.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc["id"] or f"call_{i}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"] or "{}",
                },
            }
            for i, tc in enumerate(result.tool_calls)
        ]
    return msg


def tool_message(tool_call_id: str, name: str, output: str) -> ChatMessage:
    """构造一条 tool 结果消息。"""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id or "call_0",
        "name": name,
        "content": output,
    }


__all__ = [
    "ChatMessage",
    "ToolCall",
    "StreamResult",
    "assistant_message",
    "tool_message",
]
