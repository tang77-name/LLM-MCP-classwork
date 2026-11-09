# -*- coding: utf-8 -*-
"""/resume 历史会话列表面板。

交互：↑/↓ 选择 → Enter 恢复 → Esc 取消（返回值：会话 id 或 ``None``）。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from core.sessions import SessionRecord

_HEADING = "#4aa5ff"


def _stamp(record: SessionRecord) -> str:
    """把 UTC ISO 时间显示为 ``YYYY-MM-DD HH:MM``（本地时区）。"""
    raw = record.updated_at or record.created_at or ""
    try:
        from datetime import datetime

        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is not None:
            moment = moment.astimezone()
        return moment.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return raw.replace("T", " ")[:16]


def _option_text(record: SessionRecord) -> Text:
    text = Text()
    text.append(record.title or "（未命名会话）", style="bold")
    text.append("\n")
    text.append(
        f"{_stamp(record)} · {record.message_count} 条消息 · {record.id}",
        style="dim",
    )
    return text


class ResumeScreen(ModalScreen[Optional[str]]):
    """历史会话选择面板（上下键选择、回车恢复、Esc 取消）。"""

    BINDINGS = [Binding("escape", "cancel", "取消")]

    CSS = """
    ResumeScreen { align: center middle; background: $background 60%; }
    #resume-dialog {
        width: 84;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $panel;
        padding: 1;
    }
    #resume-title { height: auto; padding: 0 1 1 1; }
    #resume-options { height: auto; max-height: 18; }
    #resume-hint { height: 1; color: $text-muted; padding: 0 1; }
    """

    def __init__(self, records: Sequence[SessionRecord]) -> None:
        super().__init__()
        self._records: List[SessionRecord] = list(records)

    def compose(self) -> ComposeResult:
        title = Text()
        title.append(f"历史会话 · {len(self._records)} 条\n", style=f"bold {_HEADING}")
        title.append("选择一条会话恢复继续对话", style="dim")
        options = [
            Option(_option_text(record), id=record.id)
            for record in self._records
        ]
        with Vertical(id="resume-dialog"):
            yield Static(title, id="resume-title")
            yield OptionList(*options, id="resume-options")
            yield Static("↑/↓ 选择   Enter 恢复   Esc 取消", id="resume-hint")

    def on_mount(self) -> None:
        options = self.query_one("#resume-options", OptionList)
        options.focus()
        if self._records:
            options.highlighted = 0
        else:
            options.styles.height = 3

    @on(OptionList.OptionSelected)
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        index = event.option_index
        if 0 <= index < len(self._records):
            self.dismiss(self._records[index].id)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["ResumeScreen"]
