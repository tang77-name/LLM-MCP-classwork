# -*- coding: utf-8 -*-
"""帮助弹窗（Modal）：使用方式 / 命令表 / 快捷键表。

命令表数据来自 :data:`core.commands.COMMAND_TABLE`（与各前端共享），
排版属于 TUI 表现层。
"""

from __future__ import annotations

from rich import box
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from core.commands import command_rows

_HEADING = "bold #4aa5ff"
_KEY_STYLE = "bold yellow"

USAGE_LINES = (
    "在底部输入框输入问题，Enter 发送；输入 / 会自动弹出命令补全面板，",
    "↑/↓ 选择命令、Enter 补全，再次 Enter 执行。",
    "模型需要查看/修改文件时会自动调用工具，工具卡片可点击展开。",
    "F2 或 /market 打开本地技能市场，可搜索、安装、卸载技能。",
    "/save 保存当前会话，/resume 打开历史会话面板（↑/↓ 选择、Enter 恢复、",
    "Esc 取消），/new 新建会话时会自动保存当前会话，/export 导出 Markdown。",
    "Esc 中断当前生成；已完成的问答才会写入历史。",
    "状态栏实时显示上下文占用（k/窗口 · 百分比）；达到 80% 后",
    "需先执行 /compact 归档并压缩历史，才能继续提问；",
    "压缩摘要缺少的细节可用 recallContext 工具检索原始归档。",
    "右侧栏显示已连接的 MCP Server 与它们提供的工具；MCP 在后台异步连接，",
    "进入界面后会自动刷新。项目根目录的 AGENTS.md 会在会话首次加载时",
    "注入一次并全程复用。",
)

KEY_BINDINGS = (
    ("Enter", "发送消息 / 补全斜杠命令"),
    ("↑ / ↓", "在斜杠命令补全面板中切换"),
    ("Esc", "中断当前生成（弹窗内为关闭）"),
    ("F1", "打开本帮助"),
    ("F2", "打开本地技能市场"),
    ("Ctrl+Q", "退出"),
    ("Ctrl+L", "清屏（保留历史）"),
    ("Ctrl+N", "新建会话（自动保存当前会话）"),
    ("Ctrl+S", "保存当前会话"),
    ("Ctrl+R", "恢复历史会话"),
    ("Ctrl+B", "显示 / 隐藏侧栏"),
    ("Ctrl+P", "命令面板（输入 / 并弹出补全）"),
)


def _heading(text: str) -> Text:
    out = Text()
    out.append(text, style=_HEADING)
    out.append("\n")
    return out


def _bullets(items) -> Text:
    out = Text()
    for item in items:
        out.append("  • ")
        out.append(item)
        out.append("\n")
    out.append("\n")
    return out


def _commands_table() -> Table:
    table = Table(
        box=box.SQUARE, show_header=True, header_style=_HEADING,
        padding=(0, 1), expand=False,
    )
    table.add_column("命令", style=_KEY_STYLE, no_wrap=True)
    table.add_column("作用")
    for name, summary in command_rows():
        table.add_row(name, summary)
    return table


def _keys_table() -> Table:
    table = Table(
        box=box.SQUARE, show_header=True, header_style=_HEADING,
        padding=(0, 1), expand=False,
    )
    table.add_column("按键", style=_KEY_STYLE, no_wrap=True)
    table.add_column("作用")
    for key, summary in KEY_BINDINGS:
        table.add_row(key, summary)
    return table


def build_help_renderable() -> Group:
    """帮助正文（弹窗主体内容）。"""
    return Group(
        _heading("使用方式"),
        _bullets(USAGE_LINES),
        _heading("命令"),
        _commands_table(),
        Text("\n"),
        _heading("快捷键"),
        _keys_table(),
        Text("\n"),
    )


class HelpScreen(ModalScreen[None]):
    """`/help` 与 F1 打开的帮助弹窗。"""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 70%;
    }
    HelpScreen > #help-panel {
        width: 90%;
        max-width: 100;
        height: 90%;
        max-height: 40;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #help-title {
        height: auto;
        text-style: bold;
        margin-bottom: 1;
    }
    #help-body {
        height: 1fr;
    }
    #help-close {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "关闭", show=False),
        Binding("f1", "close", "关闭", show=False),
        Binding("ctrl+q", "quit", "退出", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-panel"):
            yield Static(
                Text.assemble(("Zagent ", "bold"), ("帮助", "bold #4aa5ff")),
                id="help-title",
            )
            with VerticalScroll(id="help-body"):
                yield Static(build_help_renderable())
            yield Button("关闭 (Esc)", variant="primary", id="help-close")

    @on(Button.Pressed, "#help-close")
    def _close_button(self) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


__all__ = ["HelpScreen", "build_help_renderable"]
