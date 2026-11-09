# -*- coding: utf-8 -*-
"""首次运行提示对话框。

当前目录没有 ``config.ini`` 时，``load_settings`` 会自动生成一份带 mock
信息的完整配置，并通过 ``Settings.startup_notice`` 传递提示文案。TUI 在
首屏挂载后弹出本对话框，告知用户“修改配置后重新启动才能正常工作”。
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

_HEADING = "#4aa5ff"


class StartupScreen(ModalScreen[bool]):
    """配置生成提示弹窗。

    关闭时返回 ``True`` 表示退出应用，``False`` 表示继续使用（LLM 可能不可用）。
    """

    BINDINGS = [Binding("escape", "keep_going", "关闭")]

    CSS = """
    StartupScreen { align: center middle; background: $background 60%; }
    #startup-dialog {
        width: 80;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $panel;
        padding: 1 2;
    }
    #startup-message { height: auto; }
    #startup-buttons {
        height: auto;
        align-horizontal: right;
        padding-top: 1;
    }
    #startup-buttons Button { margin-left: 2; }
    """

    def __init__(self, message: str, config_path: str = "") -> None:
        super().__init__()
        self.message = message
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        text = Text()
        text.append("首次运行：已生成配置文件\n\n", style=f"bold {_HEADING}")
        text.append(self.message + "\n")
        if self.config_path:
            text.append(f"\n配置文件：{self.config_path}\n", style="dim")
        text.append(
            "\n修改 api_key 等真实信息后，请重新启动 Zagent 才能正常使用。",
            style="bold yellow",
        )
        with Vertical(id="startup-dialog"):
            yield Static(text, id="startup-message")
            with Horizontal(id="startup-buttons"):
                yield Button("退出（推荐）", variant="primary", id="startup-exit")
                yield Button("仍然继续", id="startup-continue")

    def on_mount(self) -> None:
        self.query_one("#startup-exit", Button).focus()

    @on(Button.Pressed)
    def _on_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "startup-exit")

    def action_keep_going(self) -> None:
        self.dismiss(False)


__all__ = ["StartupScreen"]
