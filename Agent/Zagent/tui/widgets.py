# -*- coding: utf-8 -*-
"""TUI 界面构件：消息块与可展开的工具调用卡片。

这些构件只消费事件数据，不含任何业务逻辑。
"""

from __future__ import annotations

import json
from time import monotonic
from typing import Optional

from rich.text import Text
from textual import on
from textual.containers import Vertical
from textual.events import Click
from textual.widgets import Static


def _fmt_duration(ms: float) -> str:
    """毫秒时长的展示文本。"""
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.2f} s"


def tool_signature(name: str, arguments: str) -> str:
    """把工具调用渲染成单行签名：``isLeapYear(year=2026)``。"""
    try:
        parsed = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    parts = []
    for key, value in parsed.items():
        if isinstance(value, str):
            shown = " ".join(value.split())
            if len(shown) > 40:
                shown = shown[:40] + "…"
            parts.append(f"{key}={shown}")
        else:
            parts.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    signature = ", ".join(parts)
    if len(signature) > 80:
        signature = signature[:80] + "…"
    return f"{name}({signature})"


class ChatBlock(Vertical):
    """一条消息块：左侧色条 + 标签 + 内容。

    ``kind`` 决定左侧色条：``user``（橙）/ ``assistant``（蓝）/
    ``notice``（黄）/ ``error``（红）。
    """

    DEFAULT_CSS = """
    ChatBlock {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        border-left: heavy $accent;
        background: $surface;
    }
    ChatBlock.user { border-left: heavy $warning; }
    ChatBlock.assistant { border-left: heavy $accent; }
    ChatBlock.notice { border-left: heavy $warning; }
    ChatBlock.error { border-left: heavy $error; }
    ChatBlock > .block-label {
        height: auto;
        width: 100%;
        text-style: bold;
    }
    ChatBlock > .block-content {
        height: auto;
        width: 100%;
    }
    """

    def __init__(self, label: str, content: str = "", kind: str = "assistant") -> None:
        super().__init__()
        self.add_class(kind)
        self._label = label
        self._body = Static(Text(label), classes="block-label")
        self._content = Static(Text(content or ""), classes="block-content")

    def compose(self):
        yield self._body
        yield self._content

    @classmethod
    def from_text(cls, label: str, text: str, kind: str = "assistant") -> "ChatBlock":
        block = cls(label, kind=kind)
        block._content.update(Text(text or ""))
        return block

    def set_content(self, text: str, *, cursor: bool = False) -> None:
        """更新内容（``cursor=True`` 时末尾追加流式光标）。"""
        body = Text(text or "")
        if cursor:
            body.append("▌", style="bold #4aa5ff")
        self._content.update(body)

    def append_content(self, delta: str) -> None:
        """追加流式增量。"""
        current = getattr(self, "_stream_text", "")
        current += delta
        self._stream_text = current
        self.set_content(current, cursor=True)

    def finalize(self, text: Optional[str] = None) -> None:
        """结束流式写入（定稿内容，去掉光标）。"""
        if text is not None:
            self._stream_text = text
        self.set_content(getattr(self, "_stream_text", ""))


class ToolCard(Vertical):
    """工具调用卡片：头部一行摘要，点击展开参数与结果。"""

    DEFAULT_CSS = """
    ToolCard {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        border-left: heavy $success;
        background: $surface;
    }
    ToolCard.failed { border-left: heavy $error; }
    ToolCard > .card-head {
        height: auto;
        width: 100%;
    }
    ToolCard > .card-body {
        display: none;
        height: auto;
        width: 100%;
        padding: 0 0 0 1;
        color: $text-muted;
    }
    ToolCard.open > .card-body { display: block; }
    """

    def __init__(self, name: str, arguments: str) -> None:
        super().__init__()
        self._name = name
        self._arguments = arguments or ""
        self._started = monotonic()
        self._duration_ms: Optional[float] = None
        self._ok = True
        self._head = Static(classes="card-head")
        self._body = Static(Text("执行中…", style="dim"), classes="card-body")

    # -- 生命周期 -----------------------------------------------------------
    def compose(self):
        yield self._head
        yield self._body

    def on_mount(self) -> None:
        self._render_head()

    @on(Click, ".card-head")
    def _toggle(self, event: Click) -> None:
        event.stop()
        self.toggle_class("open")
        self._render_head()

    # -- 状态 ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._duration_ms is None

    def finish(self, result: str, ok: bool) -> None:
        """工具执行完毕：写入耗时、结果与成败样式。"""
        self._duration_ms = (monotonic() - self._started) * 1000
        self._ok = ok
        if not ok:
            self.add_class("failed")

        signature = tool_signature(self._name, self._arguments)
        body = Text()
        body.append("参数\n", style="bold")
        try:
            pretty = json.dumps(
                json.loads(self._arguments) if self._arguments else {},
                ensure_ascii=False, indent=2,
            )
        except json.JSONDecodeError:
            pretty = self._arguments or "{}"
        body.append(pretty + "\n")
        body.append("结果\n", style="bold")
        body.append(result or "")
        self._body.update(body)
        self._render_head()

    # -- 渲染 ---------------------------------------------------------------
    def _render_head(self) -> None:
        glyph = "▼ " if self.has_class("open") else "▶ "
        signature = tool_signature(self._name, self._arguments)

        head = Text()
        head.append(glyph, style="bold yellow")
        if self.running:
            head.append("⋯ ", style="yellow")
            head.append(signature)
            head.append(" · 执行中…", style="dim yellow")
        else:
            mark, mark_style = ("✓ ", "green") if self._ok else ("✗ ", "red")
            head.append(mark, style=mark_style)
            head.append(signature)
            head.append(f" · {_fmt_duration(self._duration_ms or 0)}", style="dim")
        self._head.update(head)


__all__ = ["ChatBlock", "ToolCard", "tool_signature"]
