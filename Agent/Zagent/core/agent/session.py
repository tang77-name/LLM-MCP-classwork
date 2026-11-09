# -*- coding: utf-8 -*-
"""会话编排：一次对话的完整问答流程（含多轮工具调用与上下文压缩）。

:class:`AgentSession` 是 UI 与 core 之间最重要的边界：

- UI 调用 ``session.ask(question)``，拿到一个事件生成器并逐个消费；
- UI 调用 ``session.compact()`` 执行 ``/compact``（归档 -> LLM 摘要 -> 替换）；
- UI 调用 ``session.clear()`` / 读取 ``session.message_count``、
  ``session.blocked``、``session.context_usage`` 管理会话状态；
- 会话内部维护消息历史、驱动 LLM 流式请求、执行工具、回填结果，
  预期内的失败统一转成 :class:`TurnError` 事件而不是抛出异常。

上下文压力治理（配合 :mod:`core.context`）：

- 每次 LLM 完成响应后发出 :class:`ContextUsage`（优先 API 报告的
  ``prompt_tokens``，否则估算）；
- 占用达到窗口的 80% 时：首次发出 ``warning=True`` 的警告，
  同时 ``blocked=True`` —— 此后普通提问会被拒绝，直到 ``/compact`` 成功。

core 不持有任何 UI 对象，也不做任何 I/O 打印 —— 因此 CLI / TUI /
Web / Desktop 可以共享同一份逻辑。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional

from ..config import Settings
from ..context import (
    ContextArchive,
    ContextMonitor,
    build_compact_messages,
    build_summary,
)
from ..errors import LLMResponseError, ZagentError
from ..events import (
    AssistantText,
    CompactEnd,
    CompactProgress,
    CompactStart,
    ContextUsage,
    Event,
    HistoryCleared,
    LimitReached,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnError,
)
from ..llm import ChatClient, StreamResult, assistant_message, tool_message
from ..llm.types import ChatMessage
from ..tools import ToolRegistry
from .agents_md import ensure_injected as _inject_agents_md
from .agents_md import is_message as _is_agents_md_message
from .prompts import SYSTEM_PROMPT

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from ..sessions import SessionStore


class AgentSession:
    """一个对话会话（历史 + 工具调用编排 + 上下文压缩）。"""

    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        settings: Settings,
        system_prompt: str = SYSTEM_PROMPT,
        *,
        store: Optional["SessionStore"] = None,
        session_id: Optional[str] = None,
        context_dir: Optional[Path] = None,
        agents_md: Optional[str] = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._settings = settings
        self._system_prompt = system_prompt
        #: AGENTS.md 项目规范内容（会话首次加载时注入一次，全程复用）
        self._agents_md = agents_md
        self._history: List[ChatMessage] = [
            {"role": "system", "content": system_prompt}
        ]
        #: 一次注入：构造时就把 AGENTS.md 追加到 System Prompt 之后
        _inject_agents_md(self._history, agents_md)
        #: 持久化仓库（None 时为纯内存会话，CLI/TUI 默认行为不变）
        self._store = store
        self._session_id = session_id
        #: 上下文监控（窗口大小来自配置；80% 阈值见 core.context）
        self._monitor = ContextMonitor(settings.context_window)
        #: 原始上下文归档仓库（/compact 写、recallContext 读）
        self._archive = ContextArchive(
            context_dir or (Path.cwd() / ".agents" / "context")
        )
        #: 是否正在压缩（压缩期间拒绝普通提问）
        self._compacting = False

    # -- 只读状态 -----------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def agents_md(self) -> Optional[str]:
        """AGENTS.md 项目规范内容（未配置时为 None）。"""
        return self._agents_md

    @property
    def message_count(self) -> int:
        return len(self._history)

    @property
    def conversation_count(self) -> int:
        """真正“聊过”的消息条数。

        不含 System Prompt，也不含一次注入的 AGENTS.md 规范消息 ——
        用于判断“历史是否为空 / 是否需要自动保存”。
        """
        return sum(
            1
            for index, message in enumerate(self._history)
            if not (
                (index == 0 and isinstance(message, dict)
                 and message.get("role") == "system")
                or _is_agents_md_message(message)
            )
        )

    @property
    def history(self) -> tuple:
        """消息历史的只读快照（UI 不应直接修改）。"""
        return tuple(self._history)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def session_id(self) -> Optional[str]:
        """绑定的持久化会话 id（纯内存会话为 None）。"""
        return self._session_id

    @property
    def context_dir(self) -> Path:
        """原始上下文归档目录（``.agents/context``）。"""
        return self._archive.directory

    @property
    def blocked(self) -> bool:
        """上下文是否已达 80% 阈值（True 时暂停接受普通用户提示词）。"""
        return self._monitor.blocked

    @property
    def compacting(self) -> bool:
        """是否处于压缩状态（压缩期间禁止普通提问与工具执行）。"""
        return self._compacting

    @property
    def context_usage(self) -> ContextUsage:
        """当前上下文占用快照（纯计算，供 UI 随时刷新显示）。"""
        return self._monitor.snapshot(self._history)

    def attach_store(
        self, store: "SessionStore", session_id: str
    ) -> None:
        """绑定持久化仓库（Web 前端打开已存会话时调用）。"""
        self._store = store
        self._session_id = session_id

    def load_history(self, messages: List[ChatMessage]) -> None:
        """用存储的消息历史重建会话（system 提示词始终取当前值）。"""
        self._history = [{"role": "system", "content": self._system_prompt}]
        for message in messages:
            if isinstance(message, dict) and message.get("role") != "system":
                self._history.append(dict(message))
        # AGENTS.md：历史里已有就直接复用；没有（老会话）则补一次
        _inject_agents_md(self._history, self._agents_md)
        self._monitor.reset()

    def persist(self) -> None:
        """把当前历史写回会话仓库（无仓库时为空操作）。"""
        if self._store is None or not self._session_id:
            return
        try:
            self._store.save(self._session_id, self._history)
        except Exception:  # noqa: BLE001 - 持久化失败不应中断对话
            pass

    def update_system_prompt(self, system_prompt: str) -> None:
        """替换 System Prompt（技能市场安装 / 卸载后刷新技能清单）。"""
        self._system_prompt = system_prompt
        message = {"role": "system", "content": system_prompt}
        if self._history and self._history[0].get("role") == "system":
            self._history[0] = message
        else:
            self._history.insert(0, message)

    def clear(self) -> HistoryCleared:
        """清空历史（保留 System Prompt；AGENTS.md 规范重新注入一次）。"""
        self._history = [{"role": "system", "content": self._system_prompt}]
        _inject_agents_md(self._history, self._agents_md)
        self._monitor.reset()
        self.persist()
        return HistoryCleared(message_count=len(self._history))

    # -- 问答主流程 ---------------------------------------------------------
    def ask(self, question: str) -> Iterator[Event]:
        """处理一次用户提问（可能包含多轮工具调用），产出事件流。

        事件顺序大致为::

            TextDelta* -> AssistantText?
                -> ContextUsage
                -> (ToolCallStart -> ToolCallEnd)*   （0..N 轮）
            TurnEnd

        预期内失败（网络 / 接口错误 / 上下文已满）产出 ``TurnError`` +
        ``TurnEnd(ok=False)``；程序性错误（编程缺陷）仍会抛出。

        处于**压缩状态**或**上下文已达 80% 阈值**时直接拒绝，不追加消息。
        """
        text = (question or "").strip()
        if not text:
            return

        if self._compacting:
            yield TurnError(
                category="config",
                message="正在执行上下文压缩（/compact），请等待其完成后再提问",
            )
            yield TurnEnd(ok=False, message_count=len(self._history))
            return

        if self._monitor.blocked:
            usage = self._monitor.snapshot(self._history)
            yield TurnError(
                category="config",
                message=(
                    f"上下文占用已达 {usage.percent_text}"
                    f"（{usage.tokens_text}/{usage.window_text}），"
                    "已暂停接收新提示词；请输入 /compact 压缩上下文后继续"
                ),
            )
            yield TurnEnd(ok=False, message_count=len(self._history))
            return

        self._history.append({"role": "user", "content": text})
        ok = True

        try:
            ok = yield from self._run_turns()
        except ZagentError as exc:
            ok = False
            yield TurnError(category=exc.category, message=str(exc))

        self.persist()
        yield TurnEnd(ok=ok, message_count=len(self._history))

    # -- 内部：工具调用轮次循环 --------------------------------------------
    def _run_turns(self) -> Iterator[Event]:
        """返回本次问答是否成功完成（生成器 return 值）。"""
        max_rounds = self._settings.max_tool_rounds
        still_calling_tools = False

        for round_index in range(max_rounds):
            sent_len = len(self._history)  # 本次请求发送时的历史条数
            stream = self._client.stream(self._history, self._registry.schemas())
            for event in stream:  # 只会是 TextDelta
                yield event

            result: StreamResult = stream.result
            # 记录 API 报告的 token 用量（缺失时 monitor 自动退回估算）
            usage = result.usage or {}
            self._monitor.record(usage.get("prompt_tokens"), sent_len)
            self._history.append(assistant_message(result))

            if result.content:
                yield AssistantText(result.content, round_index=round_index)

            # 每次 LLM 完成响应后汇报上下文占用（首次越过 80% 时带 warning）
            yield self._usage_event(self._monitor.observe(self._history))

            if not result.tool_calls:
                still_calling_tools = False
                break

            still_calling_tools = True
            yield from self._run_tool_calls(result, round_index)

        # 能走到这里说明每一轮都在发起工具调用，即轮次耗尽
        if still_calling_tools:
            yield LimitReached(rounds=max_rounds)
            return False
        return True

    def _run_tool_calls(self, result: StreamResult, round_index: int) -> Iterator[Event]:
        """执行一轮内的全部工具调用，并把结果回填历史。"""
        for i, tc in enumerate(result.tool_calls):
            call_id = tc["id"] or f"call_{i}"
            yield ToolCallStart(
                tool_call_id=call_id,
                name=tc["name"],
                arguments=tc["arguments"],
                round_index=round_index,
            )

            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                if not isinstance(args, dict):
                    raise json.JSONDecodeError("not an object", "<args>", 0)
            except json.JSONDecodeError:
                args = {}
                output = f"错误：参数不是合法 JSON -> {tc['arguments']}"
                ok = False
            else:
                output = self._registry.execute(tc["name"], args)
                ok = not output.startswith("错误：")

            yield ToolCallEnd(
                tool_call_id=call_id,
                name=tc["name"],
                result=output,
                ok=ok,
                round_index=round_index,
            )
            self._history.append(tool_message(call_id, tc["name"], output))

    @staticmethod
    def _usage_event(usage) -> ContextUsage:
        return ContextUsage(
            tokens=usage.tokens,
            window=usage.window,
            percent=usage.percent,
            default_window=usage.default_window,
            over_threshold=usage.over_threshold,
            warning=usage.warning,
        )

    # -- 上下文压缩（/compact） --------------------------------------------
    def compact(self) -> Iterator[Event]:
        """手动触发上下文压缩，产出事件流。

        流程严格按顺序执行，任一步失败都**保持原有上下文不变**::

            CompactStart
              -> 归档原始上下文到 .agents/context/<编号>.jsonl（必须成功）
              -> CompactProgress
              -> 调用 LLM 生成约 5k token 的摘要（不带任何工具）
              -> CompactProgress
              -> 用摘要替换历史（保留 System Prompt）
              -> CompactEnd

        归档成功后即使摘要失败，归档文件也会保留（下次 /compact 会重新归档），
        用户可直接重试 ``/compact``。
        """
        if self._compacting:
            yield TurnError(category="config", message="上下文压缩正在进行中")
            yield TurnEnd(ok=False, message_count=len(self._history))
            return

        before = self._monitor.snapshot(self._history)
        if self.conversation_count == 0:
            yield CompactEnd(
                ok=False,
                archive_id="",
                message="历史为空，无需压缩",
                before_tokens=before.tokens,
                after_tokens=before.tokens,
            )
            return

        self._compacting = True
        archive_id = ""
        try:
            yield CompactStart()
            yield CompactProgress(
                f"开始压缩：{len(self._history)} 条消息，"
                f"当前占用 {before.tokens_text}/{before.window_text}"
                f"（{before.percent_text}）"
            )

            # 1) 保存完整原始上下文（失败则中止，历史保持原样）
            try:
                archive_id = self._archive.save(
                    self._history, session_id=self._session_id or ""
                )
            except Exception as exc:  # noqa: BLE001 - 归档失败必须可见
                yield CompactEnd(
                    ok=False,
                    archive_id="",
                    message=f"原始上下文归档失败，未执行压缩：{exc}",
                    before_tokens=before.tokens,
                    after_tokens=before.tokens,
                )
                return
            yield CompactProgress(
                f"原始上下文已归档 -> {self._archive.directory}"
                f"/{archive_id}.jsonl（{len(self._history)} 条消息）"
            )

            # 2) 调用 LLM 生成摘要（请求不携带工具，压缩期间无法 Function Call）
            yield CompactProgress("正在调用 LLM 生成工作摘要（约需数十秒）…")
            try:
                summary = self._summarize()
            except ZagentError as exc:
                yield CompactEnd(
                    ok=False,
                    archive_id=archive_id,
                    message=(
                        f"摘要生成失败，原有上下文保持不变（归档 {archive_id} 已保存），"
                        f"可重新执行 /compact：{exc}"
                    ),
                    before_tokens=before.tokens,
                    after_tokens=before.tokens,
                )
                return
            if not summary:
                yield CompactEnd(
                    ok=False,
                    archive_id=archive_id,
                    message=(
                        f"摘要内容为空，原有上下文保持不变（归档 {archive_id} 已保存），"
                        "可重新执行 /compact"
                    ),
                    before_tokens=before.tokens,
                    after_tokens=before.tokens,
                )
                return

            # 3) 用摘要替换历史（保留 System Prompt 与工具定义）
            yield CompactProgress("摘要已生成，正在替换会话上下文…")
            text = build_summary(summary, archive_id, datetime.now())
            self._history = [
                {"role": "system", "content": self._system_prompt},
                {"role": "assistant", "content": text},
            ]
            # AGENTS.md 规范全程复用：压缩后重新放回 System Prompt 之后
            _inject_agents_md(self._history, self._agents_md)
            self._monitor.reset()  # 解除输入限制
            self.persist()
            after = self._monitor.snapshot(self._history)
            yield CompactEnd(
                ok=True,
                archive_id=archive_id,
                message=(
                    f"压缩完成：{before.tokens_text} -> {after.tokens_text}"
                    f"（{before.percent_text} -> {after.percent_text}），"
                    f"历史 {len(self._history)} 条消息；"
                    f"原始上下文见归档 {archive_id}"
                ),
                before_tokens=before.tokens,
                after_tokens=after.tokens,
            )
        except ZagentError as exc:  # 兜底：预期内错误也不应抛给 UI
            yield CompactEnd(
                ok=False,
                archive_id=archive_id,
                message=f"压缩失败，原有上下文保持不变：{exc}",
                before_tokens=before.tokens,
                after_tokens=before.tokens,
            )
        finally:
            self._compacting = False

    def _summarize(self) -> str:
        """调用 LLM 对当前历史做一次“无工具”的摘要生成。"""
        stream = self._client.stream(build_compact_messages(self._history), None)
        for _ in stream:  # 消费流（摘要文本不作为对话内容展示给用户）
            pass
        result: StreamResult = stream.result
        if result.tool_calls:  # 压缩请求未携带工具，出现调用即协议异常
            raise LLMResponseError(
                "压缩请求返回了工具调用（不应发生），请重试 /compact"
            )
        return (result.content or "").strip()


__all__ = ["AgentSession"]
