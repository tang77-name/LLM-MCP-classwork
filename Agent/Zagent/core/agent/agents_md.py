# -*- coding: utf-8 -*-
"""``AGENTS.md`` 项目规范文件的「一次注入，全程复用」支持。

策略（对应需求 5）：

1. **会话首次加载**时读取项目当前目录下的 ``AGENTS.md``；
2. 把文件内容包装成**一条消息**追加到聊天历史中（固定位置：System
   Prompt 之后、第一条用户消息之前）；
3. 该消息随历史一起持久化到会话日志（``.zagent/sessions/*.json``），
   后续轮次直接复用，不再重复注入 —— 保持前缀稳定，利于 LLM 缓存命中；
4. ``/compact`` 压缩历史时**保留**这条消息，做到“全程复用”。

为什么用 ``role="user"``：

- 存储层（SessionStore）只保留 ``user`` / ``assistant`` / ``tool`` 三种角色，
  system 消息会在持久化时被丢弃；
- OpenAI 兼容服务普遍只接受第一条 ``system`` 消息，多条可能报 400；
- 用固定标记 ``MARKER`` 开头，便于在历史中识别与去重。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

#: 项目规范文件名（读取自项目当前目录）
AGENTS_FILENAME = "AGENTS.md"
#: 注入消息的内容标记（识别这条消息的唯一前缀）
MARKER = "[项目规范 AGENTS.md]"

#: 注入消息的引导语（标记之后、正文之前的固定说明）
PREFIX = (
    f"{MARKER}\n\n"
    "以下内容在会话开始时已注入，请在整个会话中持续遵守：\n\n"
)


def load_agents_md(root) -> Optional[str]:
    """读取 ``<root>/AGENTS.md``。

    文件不存在、为空白或读取失败时返回 ``None``（不报错、不阻断会话）。
    """
    try:
        path = Path(root) / AGENTS_FILENAME
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text if text.strip() else None


def build_message(content: str) -> dict:
    """把 AGENTS.md 内容包装成一条可写入历史 / 会话日志的消息。"""
    body = str(content or "").strip()
    return {"role": "user", "content": PREFIX + body}


def find_index(history: Sequence[dict]) -> Optional[int]:
    """返回历史中 AGENTS.md 消息的下标；不存在时返回 ``None``。"""
    for index, message in enumerate(history):
        if is_message(message):
            return index
    return None


def is_message(message) -> bool:
    """判断一条消息是否为注入的 AGENTS.md 规范消息。"""
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    return isinstance(content, str) and content.startswith(MARKER)


def ensure_injected(history: List[dict], content: Optional[str]) -> bool:
    """确保 ``history`` 中包含 AGENTS.md 消息（System Prompt 之后）。

    :param history: 会话历史（就地修改）
    :param content: AGENTS.md 文件内容；为空时不做任何事
    :return: 是否发生了插入
    """
    if not content or not str(content).strip():
        return False
    if find_index(history) is not None:
        return False
    # 固定插在 System Prompt 之后（没有 system 时放最前）
    position = (
        1
        if history
        and isinstance(history[0], dict)
        and history[0].get("role") == "system"
        else 0
    )
    history.insert(position, build_message(content))
    return True


__all__ = [
    "AGENTS_FILENAME",
    "MARKER",
    "PREFIX",
    "load_agents_md",
    "build_message",
    "find_index",
    "is_message",
    "ensure_injected",
]
