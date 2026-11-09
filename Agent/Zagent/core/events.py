# -*- coding: utf-8 -*-
"""会话事件模型。

core 不做任何打印/渲染，只把一次问答过程表达为
:class:`~core.agent.session.AgentSession.ask` 产生的事件序列。
UI 层（cli/tui/web/desktop）是这些事件的唯一消费者，从而保证
“业务逻辑与表现层”完全解耦。

事件类型（UI 需要处理的全部输入）：

======================  ==========================================
事件                     含义
======================  ==========================================
:class:`TextDelta`      流式文本增量（逐字到达）
:class:`AssistantText`  某一轮 assistant 完整文本（增量已全部到达）
:class:`ToolCallStart`  模型发起一次工具调用
:class:`ToolCallEnd`    该工具调用执行完毕（含结果文本 / 是否成功）
:class:`LimitReached`   工具调用轮次达到上限
:class:`TurnEnd`        本次提问处理完成（无论成功与否都会发出）
:class:`TurnError`      本次提问失败（网络 / 接口错误），不会抛出异常
:class:`ContextUsage`   上下文占用快照（每次 LLM 响应后发出）
:class:`CompactStart`   /compact 开始（会话进入压缩状态）
:class:`CompactProgress`压缩过程中的阶段提示
:class:`CompactEnd`     /compact 结束（ok=True 时上下文已被替换）
======================  ==========================================
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    """所有事件的标记基类（frozen，线程安全、可安全跨线程传递）。"""


@dataclass(frozen=True)
class TextDelta(Event):
    """流式输出的一段增量文本。"""

    text: str


@dataclass(frozen=True)
class AssistantText(Event):
    """一轮模型回复的完整文本（可能为空字符串）。"""

    text: str
    round_index: int = 0


@dataclass(frozen=True)
class ToolCallStart(Event):
    """模型请求调用工具（arguments 为原始 JSON 字符串）。"""

    tool_call_id: str
    name: str
    arguments: str
    round_index: int = 0


@dataclass(frozen=True)
class ToolCallEnd(Event):
    """工具执行完成。``ok=False`` 表示执行失败（result 为错误说明）。"""

    tool_call_id: str
    name: str
    result: str
    ok: bool
    round_index: int = 0


@dataclass(frozen=True)
class LimitReached(Event):
    """单次提问内工具调用轮次达到上限，本轮被强制停止。"""

    rounds: int


@dataclass(frozen=True)
class TurnEnd(Event):
    """一次提问处理结束；``ok=False`` 时说明之前发出过 :class:`TurnError`。"""

    ok: bool
    message_count: int


@dataclass(frozen=True)
class TurnError(Event):
    """一次提问失败。core 捕获预期错误并转成事件，避免各 UI 重复 try/except。"""

    category: str  # network | api | config | tool | internal
    message: str


@dataclass(frozen=True)
class HistoryCleared(Event):
    """会话历史被清空（由 UI 发起 clear 操作后由 session 发出）。"""

    message_count: int = 0


@dataclass(frozen=True)
class ContextUsage(Event):
    """上下文占用快照（每次 LLM 完成响应后由会话发出）。

    ``warning=True`` 表示**首次**越过 80% 阈值（UI 应提示执行 /compact）；
    ``over_threshold=True`` 表示当前处于阈值之上（普通输入已被暂停）。
    """

    tokens: int              # 当前上下文占用（token）
    window: int              # 窗口大小（token）
    percent: float           # 占用比例 0~1
    default_window: bool     # 窗口是否为默认 128k（无法获知真实窗口）
    over_threshold: bool
    warning: bool = False


@dataclass(frozen=True)
class CompactStart(Event):
    """/compact 开始：会话进入压缩状态（期间拒绝普通提问与工具执行）。"""


@dataclass(frozen=True)
class CompactProgress(Event):
    """压缩过程中的阶段提示（归档完成 / 摘要生成中 / 正在替换等）。"""

    message: str


@dataclass(frozen=True)
class CompactEnd(Event):
    """/compact 结束。``ok=False`` 时原有上下文保持不变，可重新执行。"""

    ok: bool
    archive_id: str
    message: str
    before_tokens: int = 0
    after_tokens: int = 0


__all__ = [
    "Event",
    "TextDelta",
    "AssistantText",
    "ToolCallStart",
    "ToolCallEnd",
    "LimitReached",
    "TurnEnd",
    "TurnError",
    "HistoryCleared",
    "ContextUsage",
    "CompactStart",
    "CompactProgress",
    "CompactEnd",
]
