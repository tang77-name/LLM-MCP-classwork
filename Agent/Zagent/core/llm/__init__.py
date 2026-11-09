# -*- coding: utf-8 -*-
"""LLM 接入层：OpenAI 兼容 Chat Completions 的流式客户端与消息模型。"""

from .types import ChatMessage, StreamResult, ToolCall, assistant_message, tool_message
from .client import ChatClient, ChatStream

__all__ = [
    "ChatMessage",
    "StreamResult",
    "ToolCall",
    "assistant_message",
    "tool_message",
    "ChatClient",
    "ChatStream",
]
