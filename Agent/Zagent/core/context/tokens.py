# -*- coding: utf-8 -*-
"""上下文 Token 估算与占用监控。

两种用量来源（优先级从高到低）：

1. **模型 API 报告的用量**：OpenAI 兼容接口在 ``stream_options.include_usage``
   开启时，会在最后一个 SSE 分块里返回 ``usage.prompt_tokens`` —— 它精确包含
   System Prompt + 对话历史 + 工具定义与工具消息，是最可靠的数字；
2. **本地估算**：拿不到 API 用量时（服务不支持 / 老接口），把整个历史
   序列化后按“中日韩字符 1 token/字、其余 4 字符/token”粗估。

:class:`ContextMonitor` 负责把两者拼起来：记录最近一次 API 报告及其对应的
历史长度，再估算其后新增的消息，从而在任意时刻给出“当前上下文占用”。

阈值语义（见 :data:`WARN_RATIO`）：

- 达到窗口的 80% -> 首次越过时产出 ``warning=True``（UI 显示警告），
  同时进入 ``blocked``（暂停接受普通用户提示词，直到 /compact 完成）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: 无法获知模型上下文窗口时的默认值：128k（UI 需标注“默认”）
DEFAULT_CONTEXT_WINDOW = 128 * 1024

#: 上下文占用警告 / 暂停输入的阈值（80%）
WARN_RATIO = 0.8


def _is_cjk(char: str) -> bool:
    """是否为中日韩表意文字（这类字符在分词器里接近 1 字 1 token）。"""
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF    # CJK 统一汉字
        or 0x3400 <= code <= 0x4DBF  # CJK 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0x3040 <= code <= 0x30FF  # 平假名 / 片假名
        or 0xAC00 <= code <= 0xD7AF  # 谚文
        or 0x3000 <= code <= 0x303F  # 中文标点
    )


def estimate_tokens(text: str) -> int:
    """粗略估算一段文本的 token 数（无需分词器，纯标准库）。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if _is_cjk(char))
    other = len(text) - cjk
    return int(cjk + other / 4)


def message_text(message: Any) -> str:
    """把一条消息（含工具调用 / 工具结果）序列化成可估算、可检索的文本。"""
    if isinstance(message, str):
        return message
    try:
        return json.dumps(message, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - 防御性分支
        return str(message)


def estimate_messages(messages: Iterable[Any]) -> int:
    """估算一串消息的总 token 数（含 System Prompt 与工具消息）。"""
    return sum(estimate_tokens(message_text(msg)) for msg in messages)


@dataclass(frozen=True)
class ContextUsage:
    """一次上下文占用快照（UI 展示 / 事件载荷）。"""

    tokens: int            # 当前上下文占用（token）
    window: int            # 上下文窗口大小（token）
    percent: float         # 占用比例 0~1
    default_window: bool   # 窗口是否为“默认 128k”（无法获知真实窗口时）
    over_threshold: bool   # 是否已达到 80% 阈值
    warning: bool = False  # 是否为本次首次越过阈值（一次性警告）

    @property
    def percent_text(self) -> str:
        return f"{self.percent * 100:.1f}%"

    @property
    def tokens_text(self) -> str:
        return _k(self.tokens)

    @property
    def window_text(self) -> str:
        return _k(self.window) + ("(默认)" if self.default_window else "")

    @property
    def summary_text(self) -> str:
        """形如 ``12.3k/128.0k(默认) · 9.6%`` 的单行摘要。"""
        return f"{self.tokens_text}/{self.window_text} · {self.percent_text}"


def _k(tokens: int) -> str:
    """token 数 -> ``12.3k`` 形式。"""
    return f"{tokens / 1024:.1f}k"


class ContextMonitor:
    """会话上下文占用监控（记录 API 用量 + 估算增量 + 阈值状态）。"""

    def __init__(self, window: int = 0) -> None:
        #: 配置的窗口大小；0 / 负数表示未知 -> 使用默认 128k
        self._window = int(window or 0)
        #: 最近一次 API 报告的 prompt_tokens（None 表示从未拿到）
        self._api_tokens: Optional[int] = None
        #: 该报告对应的历史条数（其后新增消息用估算补齐）
        self._api_len = 0
        #: 是否已越过阈值并暂停普通输入
        self.blocked = False
        self._warned = False

    # -- 窗口 ---------------------------------------------------------------
    @property
    def window(self) -> int:
        """当前使用的窗口大小（未知时为默认 128k）。"""
        if self._window > 0:
            return self._window
        return DEFAULT_CONTEXT_WINDOW

    @property
    def default_window(self) -> bool:
        """窗口大小是否为默认值（UI 需显示“默认”提示）。"""
        return self._window <= 0

    # -- 用量 ---------------------------------------------------------------
    def record(self, prompt_tokens: Optional[int], history_len: int) -> None:
        """记录一次 LLM 请求报告的用量。

        :param prompt_tokens: API 返回的 ``usage.prompt_tokens``
                              （请求发送时刻的历史占用，含 system / 工具）
        :param history_len: 该请求发送时刻的历史消息条数
        """
        try:
            tokens = int(prompt_tokens)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if tokens > 0:
            self._api_tokens = tokens
            self._api_len = max(0, int(history_len))

    def snapshot(self, messages: Iterable[Any]) -> ContextUsage:
        """纯计算当前占用（不改变 blocked / warning 状态，供 UI 随时刷新）。"""
        return self._build(list(messages), transition=False)

    def observe(self, messages: Iterable[Any]) -> ContextUsage:
        """计算占用并推进阈值状态（每次 LLM 响应后调用一次）。"""
        return self._build(list(messages), transition=True)

    def reset(self) -> None:
        """清空状态（新建会话 / 清空历史 / 压缩完成后调用）。"""
        self._api_tokens = None
        self._api_len = 0
        self.blocked = False
        self._warned = False

    # -- 内部 ---------------------------------------------------------------
    def _build(self, messages: list, *, transition: bool) -> ContextUsage:
        if self._api_tokens is not None:
            # API 报告覆盖到第 _api_len 条，其后新增的消息用估算补齐
            tokens = self._api_tokens + estimate_messages(messages[self._api_len:])
        else:
            tokens = estimate_messages(messages)

        window = self.window
        percent = tokens / window if window > 0 else 0.0
        over = percent >= WARN_RATIO

        warning = False
        if transition:
            if over:
                warning = not self._warned
                self._warned = True
            self.blocked = over

        return ContextUsage(
            tokens=tokens,
            window=window,
            percent=percent,
            default_window=self.default_window,
            over_threshold=over,
            warning=warning,
        )


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "WARN_RATIO",
    "ContextUsage",
    "ContextMonitor",
    "estimate_tokens",
    "estimate_messages",
    "message_text",
]
