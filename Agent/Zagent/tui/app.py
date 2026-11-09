# -*- coding: utf-8 -*-
"""TUI 前端（基于 Textual 的终端图形界面）。

界面能力（对应设计稿）：

1. 顶部横幅：应用名 / 工具数 / 模型 / 工作目录 / 快捷键提示；
2. 对话区：带左侧色条的消息块（你 / Zagent），流式逐字渲染；
3. 工具卡片：绿色色条 + ``▶ √ name(args) · 耗时``，点击展开参数与结果；
4. 右侧信息栏（Ctrl+B 开关）：会话信息 + 技能 / 工具 / **MCP Server 与工具**；
5. 底部输入框 + 斜杠命令补全面板 + 状态栏 + Footer 快捷键；
6. `/help` 或 F1 打开帮助弹窗，Esc 中断生成；
7. `/new` `/save` `/resume` `/export` 等会话管理命令（恢复面板上下键选择）；
8. MCP Server 后台异步连接：**先进界面**，加载完成后自动刷新侧栏 MCP 区域；
9. 首次运行自动生成 config.ini 时弹出提示对话框。

架构不变：只消费 core 的事件流（worker 线程 -> ``call_from_thread``
回 UI 线程渲染），core 不感知本界面。
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Dict, List, Optional

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, get_current_worker

from core.agent import AgentService, AgentSession
from core.agent.agents_md import is_message as _is_agents_message
from core.commands import Command, parse, suggest
from core.events import (
    AssistantText,
    CompactEnd,
    CompactProgress,
    CompactStart,
    ContextUsage,
    HistoryCleared,
    LimitReached,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnError,
)
from core.ports import Frontend
from core.sessions import history_to_markdown

from .help import HelpScreen
from .market import MarketScreen
from .resume import ResumeScreen
from .startup import StartupScreen
from .widgets import ChatBlock, ToolCard

_HEADING = "#4aa5ff"
_INPUT_PLACEHOLDER = "输入问题后 Enter 发送；输入 / 查看命令补全；F2 技能市场；Esc 中断生成"


class _Done:
    """worker 结束标记（含取消场景，此时可能收不到 TurnEnd）。"""


class _McpReady:
    """MCP 后台连接完成标记（用于动态刷新侧栏 MCP 区域）。"""


class ZagentApp(App):
    """Zagent 的 Textual 应用。"""

    TITLE = "Zagent"

    CSS = """
    Screen { background: $surface; }
    #body { height: 1fr; }
    #main { width: 1fr; height: 100%; }
    #transcript {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        scrollbar-gutter: stable;
    }
    #banner {
        height: auto;
        margin: 1 2 0 2;
        padding: 0 1;
        border: solid $accent;
        background: $panel;
    }
    #sidebar {
        width: 42;
        height: 100%;
        border-left: solid $accent;
        background: $panel;
    }
    #session-info { height: auto; padding: 1 1 0 1; }
    #tools-scroll { height: 1fr; padding: 0 1; }
    #prompt {
        height: 3;
        border: solid $accent;
        background: $panel;
    }
    #cmd-panel {
        display: none;
        height: auto;
        margin: 0 2;
        border: solid $accent;
        background: $panel;
    }
    #cmd-options {
        height: auto;
        background: $panel;
    }
    #status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+l", "clear_screen", "清屏"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+s", "save_session", "保存"),
        Binding("ctrl+r", "resume", "恢复"),
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("f1", "help", "帮助"),
        Binding("f2", "market", "市场"),
        # 斜杠命令补全面板：↑/↓ 选择、Enter 补全（无面板时 Enter 正常发送）
        Binding("up", "suggest_prev", show=False),
        Binding("down", "suggest_next", show=False),
        Binding("enter", "prompt_submit", show=False, priority=True),
        Binding("escape", "interrupt", "中断", show=False),
        Binding("ctrl+p", "command_palette", "palette"),
    ]

    def __init__(self, service: AgentService) -> None:
        super().__init__()
        self.service = service
        self.session: AgentSession = service.new_session()
        self._session_id = secrets.token_hex(4)
        #: 持久化会话记录 id（/save 后绑定，之后每轮自动写回）
        self._record_id: Optional[str] = None

        # 状态栏 / 侧栏统计
        self._state = "就绪"          # 就绪 | 生成中 | 已中断
        self._turn_no = 0             # 已完成轮次
        self._turn_tools = 0          # 本轮工具调用次数
        self._turn_start: Optional[float] = None
        self._last_duration = 0.0     # 上一轮耗时（秒）

        # 流式渲染状态
        self._stream_block: Optional[ChatBlock] = None
        self._stream_text = ""
        self._tool_cards: Dict[str, ToolCard] = {}

        # 斜杠命令补全状态
        self._suggestions: List = []

        self._worker: Optional[Worker] = None

    # ------------------------------------------------------------------ UI 组装
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="main"):
                yield self._banner()
                with VerticalScroll(id="transcript"):
                    pass  # 消息块 / 工具卡片运行期挂载
            with Vertical(id="sidebar"):
                yield Static("", id="session-info")
                with VerticalScroll(id="tools-scroll"):
                    yield Static("", id="tools-list")
        with Vertical(id="cmd-panel"):
            yield OptionList(id="cmd-options")
        yield Input(placeholder=_INPUT_PLACEHOLDER, id="prompt")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        info = self.service.describe()
        cwd = os.getcwd()
        self.sub_title = f"{info['model_name']} · {cwd}"
        self._refresh_library()
        self._refresh_sidebar()
        self._refresh_status()
        self.query_one("#prompt", Input).focus()

        # 首次运行生成 config.ini：弹出提示对话框（修改后重启才能正常工作）
        if self.service.settings.startup_notice:
            self.call_after_refresh(self._show_startup_notice)

        # MCP：先进入界面，再在后台等待连接完成并动态刷新
        if self.service.mcp_loading:
            self._notice("MCP Server 正在后台连接，完成后会自动刷新右侧栏…",
                         label="MCP")
            self._watch_mcp()

    def _show_startup_notice(self) -> None:
        self.push_screen(
            StartupScreen(
                self.service.settings.startup_notice,
                config_path=self.service.settings.config_path,
            ),
            self._on_startup_choice,
        )

    def _on_startup_choice(self, exit_requested: Optional[bool]) -> None:
        if exit_requested:
            self.exit()

    @work(thread=True, group="mcp", exclusive=True)
    def _watch_mcp(self) -> None:
        """后台等待 MCP 连接结束，完成后回投 UI 刷新。"""
        try:
            self.service.wait_mcp(timeout=180.0)
        except Exception:  # noqa: BLE001 - 等待失败也不应影响界面
            pass
        self._post(_McpReady())

    # -------------------------------------------------------------- 横幅 / 重置
    def _banner(self) -> Static:
        return Static(self._banner_text(), id="banner")

    def _banner_text(self) -> Text:
        info = self.service.describe()
        text = Text()
        text.append("Zagent", style=f"bold {_HEADING}")
        text.append("  ·  ")
        text.append(f"{len(info['tools'])} 个工具", style="bold")
        text.append("  ·  ")
        text.append(f"{len(info['skills'])} 个技能", style="bold")
        text.append("  ·  ")
        text.append(info["model_name"], style="bold")
        text.append("\n工作目录  ")
        text.append(os.getcwd())
        text.append("\n")
        text.append(
            "Enter 发送   / 命令补全   F2 技能市场   Ctrl+S 保存   Esc 中断   Ctrl+B 侧栏",
            style="dim",
        )
        return text

    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _reset_transcript(self) -> None:
        """只清对话消息（横幅固定在滚动区之外，不重建，避免重复 id）。"""
        self._transcript().remove_children()

    def _append_block(self, widget) -> None:
        self._transcript().mount(widget)

    def _scroll_end(self) -> None:
        """滚到底部（布局刷新后执行，保证内容高度已更新）。"""
        self.call_after_refresh(self._do_scroll)

    def _do_scroll(self) -> None:
        try:
            self._transcript().scroll_end(animate=False)
        except Exception:  # pragma: no cover - 界面关闭竞态
            pass

    # ---------------------------------------------------------------- 输入分发
    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self._hide_suggestions()
        if not text:
            return

        command, argument = parse(text)
        if command is Command.EXIT:
            self.exit()
            return
        if command is Command.HELP:
            self.action_help()
            return
        if command is Command.CLEAR:
            self.action_clear_screen()
            return
        if command is Command.NEW:
            self.action_new_session()
            return
        if command is Command.SAVE:
            self.action_save_session(argument)
            return
        if command is Command.RESUME:
            self.action_resume()
            return
        if command is Command.EXPORT:
            self.action_export(argument)
            return
        if command is Command.COMPACT:
            self._start_compact()
            return
        if command is Command.TOOLS:
            self._show_tools()
            return
        if command is Command.MCP:
            self._show_mcp()
            return
        if command is Command.MARKET:
            query = argument
            if query.casefold().startswith("search "):
                query = query[len("search "):].strip()
            self.action_market(query)
            return
        if command is Command.CWD:
            self._show_cwd()
            return
        if command is Command.MODEL:
            self._show_model()
            return
        if command is Command.CONFIG:
            self._show_config()
            return

        if self._worker is not None and not self._worker.is_finished:
            self.notify("上一轮仍在生成中，按 Esc 可中断", severity="warning")
            return

        # 上下文已达 80%：暂停普通输入，直到 /compact 成功
        if self.session.blocked:
            usage = self.session.context_usage
            self._notice(
                f"⚠ 上下文占用已达 {usage.percent_text}"
                f"（{usage.tokens_text}/{usage.window_text}），"
                "已暂停接收新提示词。请输入 /compact 压缩上下文后继续。",
                label="上下文",
            )
            self.notify("上下文已满，请先执行 /compact", severity="warning")
            self._scroll_end()
            return

        self._start_turn(text)

    # ------------------------------------------------------- 斜杠命令补全面板
    def _suggestions_widget(self) -> Optional[OptionList]:
        try:
            return self.query_one("#cmd-options", OptionList)
        except Exception:  # pragma: no cover - 界面未挂载
            return None

    def _panel_visible(self) -> bool:
        """补全面板是否处于可交互状态（可见 + 输入框有焦点）。"""
        if not self._suggestions:
            return False
        try:
            panel = self.query_one("#cmd-panel", Vertical)
            prompt = self.query_one("#prompt", Input)
        except Exception:  # pragma: no cover - 界面未挂载
            return False
        return bool(panel.display) and prompt.has_focus

    def _update_suggestions(self, text: str) -> None:
        """按输入内容刷新补全面板（仅对 "/" 开头且未输入空格的文本生效）。"""
        raw = text or ""
        if not raw.startswith("/") or any(ch.isspace() for ch in raw):
            self._hide_suggestions()
            return
        matches = suggest(raw)
        if not matches:
            self._hide_suggestions()
            return
        options = self._suggestions_widget()
        panel = self.query_one("#cmd-panel", Vertical)
        self._suggestions = list(matches)
        options.clear_options()
        for spec in matches:
            line = Text()
            line.append(f"{spec.name:<10}", style="bold")
            line.append(spec.summary, style="dim")
            options.add_option(Option(line))
        options.highlighted = 0
        options.styles.height = min(len(matches), 8)
        panel.display = True

    def _hide_suggestions(self) -> None:
        self._suggestions = []
        options = self._suggestions_widget()
        if options is not None:
            options.clear_options()
        try:
            self.query_one("#cmd-panel", Vertical).display = False
        except Exception:  # pragma: no cover - 界面未挂载
            pass

    def _move_suggestion(self, delta: int) -> None:
        if not self._panel_visible():
            return
        options = self._suggestions_widget()
        if options is None or not self._suggestions:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % len(self._suggestions)

    def action_suggest_prev(self) -> None:
        """↑：在补全面板中向上切换。"""
        self._move_suggestion(-1)

    def action_suggest_next(self) -> None:
        """↓：在补全面板中向下切换。"""
        self._move_suggestion(1)

    def action_prompt_submit(self) -> None:
        """Enter：补全面板可见时先补全命令，否则交回输入框默认提交。"""
        if not self._panel_visible():
            raise SkipAction()  # 不拦截：让输入框按默认逻辑提交
        options = self._suggestions_widget()
        index = options.highlighted if options is not None else None
        if index is None or index >= len(self._suggestions):
            raise SkipAction()
        spec = self._suggestions[index]
        prompt = self.query_one("#prompt", Input)
        if prompt.value.strip().lower() == spec.name:
            raise SkipAction()  # 已是完整命令：直接提交执行
        self._accept_suggestion(index)

    def _accept_suggestion(self, index: int) -> None:
        if not (0 <= index < len(self._suggestions)):
            return
        spec = self._suggestions[index]
        prompt = self.query_one("#prompt", Input)
        prompt.value = spec.name + " "
        prompt.cursor_position = len(prompt.value)
        self._hide_suggestions()
        prompt.focus()

    @on(Input.Changed, "#prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        self._update_suggestions(event.value)

    @on(OptionList.OptionSelected, "#cmd-options")
    def _on_suggestion_selected(self, event: OptionList.OptionSelected) -> None:
        self._accept_suggestion(event.option_index)

    def action_command_palette(self) -> None:
        """Ctrl+P：打开斜杠命令补全面板（等价于输入 "/"）。"""
        prompt = self.query_one("#prompt", Input)
        if prompt.disabled:
            return
        prompt.value = "/"
        prompt.cursor_position = 1
        prompt.focus()
        self._update_suggestions("/")

    def _start_turn(self, question: str) -> None:
        self._finalize_stream()
        self._append_block(ChatBlock("你", question, kind="user"))
        self._turn_tools = 0
        self._turn_start = monotonic()
        self._set_state("生成中")
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        self._refresh_status()
        self._scroll_end()
        self._worker = self._ask(question)

    # -------------------------------------------------- 事件驱动（worker -> UI）
    @work(thread=True, exclusive=True, group="ask")
    def _ask(self, question: str) -> None:
        """后台线程消费 core 事件生成器，逐事件回投 UI 线程。"""
        worker = get_current_worker()
        try:
            for event in self.session.ask(question):
                if worker.is_cancelled:
                    self._post(_Done())
                    return
                self._post(event)
        except Exception as exc:  # 未预期错误不应让界面崩溃
            self._post(TurnError(category="internal", message=str(exc)))
            self._post(_Done())
            return
        self._post(_Done())

    def _start_compact(self) -> None:
        """/compact：进入压缩状态（归档 -> LLM 摘要 -> 替换上下文）。"""
        if self._worker is not None and not self._worker.is_finished:
            self.notify("上一轮仍在生成中，按 Esc 可中断后再压缩", severity="warning")
            return
        if self.session.compacting:
            self.notify("上下文压缩正在进行中", severity="warning")
            return
        self._finalize_stream()
        self._set_state("压缩中")
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        self._refresh_status()
        self._worker = self._compact()

    @work(thread=True, exclusive=True, group="compact")
    def _compact(self) -> None:
        """后台线程执行压缩（期间 core 拒绝普通提问与工具执行）。"""
        worker = get_current_worker()
        try:
            for event in self.session.compact():
                if worker.is_cancelled:
                    self._post(_Done())
                    return
                self._post(event)
        except Exception as exc:  # 未预期错误不应让界面崩溃
            self._post(TurnError(category="internal", message=str(exc)))
            self._post(_Done())
            return
        self._post(_Done())

    def _post(self, payload: object) -> None:
        try:
            self.call_from_thread(self._handle, payload)
        except RuntimeError:
            pass  # 应用已关闭

    def _handle(self, payload: object) -> None:
        if isinstance(payload, _McpReady):
            self._on_mcp_ready()
            return

        if isinstance(payload, _Done):
            self._finalize_stream()
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            prompt.focus()
            self._set_state("就绪")
            self._refresh_sidebar()
            self._refresh_status()
            return

        event = payload
        if isinstance(event, TextDelta):
            self._on_delta(event.text)
        elif isinstance(event, AssistantText):
            self._finalize_assistant(event.text)
        elif isinstance(event, ToolCallStart):
            self._finalize_stream()
            card = ToolCard(event.name, event.arguments)
            self._tool_cards[event.tool_call_id] = card
            self._append_block(card)
            self._turn_tools += 1
            self._refresh_status()
        elif isinstance(event, ToolCallEnd):
            card = self._tool_cards.pop(event.tool_call_id, None)
            if card is not None:
                card.finish(event.result, event.ok)
        elif isinstance(event, LimitReached):
            self._notice(
                f"⚠ 工具调用轮次达到上限（{event.rounds}），已停止本轮。",
                label="警告",
            )
        elif isinstance(event, TurnError):
            self._finalize_stream()
            self._append_block(
                ChatBlock("错误", f"[{event.category}] {event.message}", kind="error")
            )
            self.notify(event.message[:120], severity="error", title="错误")
        elif isinstance(event, HistoryCleared):
            self._reset_transcript()
            self._notice("历史记录已清空。", label="命令")
        elif isinstance(event, ContextUsage):
            self._refresh_status()
            if event.warning:
                window = f"{event.window / 1024:.0f}k"
                if event.default_window:
                    window += "（默认）"
                self._notice(
                    f"⚠ 上下文已占用 {event.percent * 100:.1f}%"
                    f"（{event.tokens / 1024:.1f}k/{window}），"
                    "接近窗口上限。请输入 /compact 压缩上下文。",
                    label="警告",
                )
                self.notify(
                    "上下文占用已达 80%，请执行 /compact",
                    severity="warning", title="上下文",
                )
        elif isinstance(event, CompactStart):
            self._set_state("压缩中")
        elif isinstance(event, CompactProgress):
            self._notice(event.message, label="压缩")
        elif isinstance(event, CompactEnd):
            if event.ok:
                self._notice(f"✔ {event.message}", label="压缩")
                self.notify("上下文压缩完成", title="压缩")
            else:
                self._notice(f"✘ {event.message}", label="压缩")
                self.notify(event.message[:120], severity="warning",
                            title="压缩失败")
        elif isinstance(event, TurnEnd):
            self._finalize_stream()
            self._turn_no += 1
            if self._turn_start is not None:
                self._last_duration = monotonic() - self._turn_start
            self._refresh_sidebar()
            self._refresh_status()

        self._scroll_end()

    # -- 流式渲染 -----------------------------------------------------------
    def _on_delta(self, text: str) -> None:
        if self._stream_block is None:
            self._stream_block = ChatBlock("Zagent", kind="assistant")
            self._stream_text = ""
            self._append_block(self._stream_block)
        self._stream_block.append_content(text)

    def _finalize_assistant(self, text: str) -> None:
        if self._stream_block is None:
            if text:
                self._append_block(ChatBlock("Zagent", text, kind="assistant"))
            return
        self._stream_block.finalize(text if text else None)
        self._stream_block = None
        self._stream_text = ""

    def _finalize_stream(self) -> None:
        if self._stream_block is not None:
            self._stream_block.finalize()
            self._stream_block = None
            self._stream_text = ""

    def _notice(self, text: str, label: str = "提示") -> None:
        self._append_block(ChatBlock(label, text, kind="notice"))

    # ------------------------------------------------------------------ 命令输出
    def _show_tools(self) -> None:
        lines = []
        for spec in self.service.registry.schemas():
            fn = spec["function"]
            lines.append(f"• {fn['name']}\n  {fn['description']}")
        self._notice("\n".join(lines), label="工具")

    def _show_cwd(self) -> None:
        self._notice(os.getcwd(), label="工作目录")

    def _show_model(self) -> None:
        info = self.service.settings.masked()
        self._notice(f"模型 {info['model_name']}\n接口 {info['base_url']}",
                     label="模型")

    def _show_mcp(self) -> None:
        """/mcp：MCP Server 连接状态与可用工具清单。"""
        if self.service.mcp_loading:
            self._notice(
                "MCP Server 正在后台连接，请稍候（完成后侧栏会自动刷新）。",
                label="MCP",
            )
            return
        rows = self.service.mcp_statuses()
        if not rows:
            self._notice(
                "未配置 MCP Server：当前目录没有 .mcp.json，或解析结果为空。",
                label="MCP",
            )
            return
        lines = []
        for row in rows:
            if row.ok:
                lines.append(f"✓ {row.name}（{row.transport}）· {len(row.tools)} 个工具")
                lines.append(f"  地址：{row.target}")
                for name in row.tools:
                    lines.append(f"  • {name}")
            else:
                lines.append(f"✗ {row.name}（{row.transport or '未知类型'}）连接失败")
                lines.append(f"  {row.error}")
        lines.append("")
        lines.append(f"配置文件：{self.service.mcp.config_path}")
        self._notice("\n".join(lines), label="MCP")

    def _show_config(self) -> None:
        """/config：显示当前生效配置（密钥脱敏）。"""
        info = self.service.settings.masked()
        lines = [f"配置文件：{info['config_path'] or '（内置默认值）'}"]
        for key in (
            "base_url", "api_key", "model_name", "temperature", "timeout",
            "max_tool_rounds", "context_window", "search_timeout",
            "search_top_k", "search_snippet_chars", "web_host", "web_port",
        ):
            lines.append(f"{key} = {info[key]}")
        lines.append("工具参数：")
        for key, value in (info.get("tool_params") or {}).items():
            lines.append(f"  {key} = {value}")
        lines.append("MCP 参数：")
        for key, value in (info.get("mcp_params") or {}).items():
            lines.append(f"  {key} = {value}")
        self._notice("\n".join(lines), label="配置")

    # ----------------------------------------------------------- 侧栏 / 状态栏
    def _set_state(self, state: str) -> None:
        self._state = state
        self._refresh_sidebar()
        self._refresh_status()

    def _elapsed(self) -> float:
        if self._state == "生成中" and self._turn_start is not None:
            return monotonic() - self._turn_start
        return self._last_duration

    def _refresh_status(self) -> None:
        dot_style = {
            "就绪": "green",
            "生成中": "yellow",
            "压缩中": "yellow",
        }.get(self._state, "red")
        status = Text()
        status.append("● ", style=dot_style)
        status.append(self._state, style="bold")
        if self._turn_no:
            status.append(f" · {self._turn_no} 轮", style="dim")
        status.append(" │ ")
        status.append(self.service.settings.model_name, style="cyan")
        status.append(f" │ 工具 {self._turn_tools} 次")
        status.append(f" │ {self._elapsed():.1f}s")

        # 上下文占用（醒目：状态栏常驻，越阈值变红）
        usage = self.session.context_usage
        ratio = usage.percent
        ctx_style = "red" if ratio >= 0.8 else ("yellow" if ratio >= 0.6 else "green")
        status.append(" │ 上下文 ", style="dim")
        status.append(
            f"{usage.tokens_text}/{usage.window_text} · {usage.percent_text}",
            style=f"bold {ctx_style}",
        )
        if usage.over_threshold:
            status.append(" ⚠需/compact", style="bold red")
        status.append(" │ Esc 中断", style="dim")
        self.query_one("#status", Static).update(status)

    def _refresh_sidebar(self) -> None:
        settings = self.service.settings
        info = Table(
            show_header=False, box=None, show_edge=False,
            padding=(0, 1), pad_edge=False, collapse_padding=True,
        )
        info.add_column("k", style="dim", no_wrap=True, width=6)
        info.add_column("v", overflow="fold")
        info.add_row("模型", settings.model_name)
        info.add_row("接口", settings.base_url)
        info.add_row("目录", os.getcwd())
        info.add_row("技能", f"{len(self.service.skills)} 个")
        info.add_row("市场", str(self.service.market.market_dir))
        info.add_row(
            "会话",
            self._record_id or f"{self._session_id}（未保存）",
        )
        mcp_rows = self.service.describe().get("mcp") or []
        mcp_ok = sum(1 for row in mcp_rows if row.get("ok"))
        info.add_row(
            "MCP",
            "加载中…" if self.service.mcp_loading
            else f"{mcp_ok}/{len(mcp_rows)} 个 Server",
        )
        info.add_row("规范", "AGENTS.md" if self.service.agents_md else "—")
        info.add_row("轮次", str(self._turn_no))
        info.add_row("消息", str(self.session.message_count))
        usage = self.session.context_usage
        ctx_style = (
            "red" if usage.percent >= 0.8
            else ("yellow" if usage.percent >= 0.6 else "green")
        )
        info.add_row(
            "上下文",
            Text(
                f"{usage.tokens_text}/{usage.window_text} · {usage.percent_text}",
                style=f"bold {ctx_style}",
            ),
        )
        if usage.over_threshold:
            info.add_row("提示", Text("已达 80%，请输入 /compact", style="red"))
        info.add_row("上限", f"{settings.max_tool_rounds} 次模型调用/轮")
        info.add_row("状态", self._state)

        self.query_one("#session-info", Static).update(
            Group(
                Text("会话\n", style=f"bold {_HEADING}"),
                info,
            )
        )

    def _build_library_text(self) -> Group:
        skills = self.service.skills
        skill_table = Table(
            show_header=False, box=None, show_edge=False,
            padding=(0, 0), pad_edge=False, collapse_padding=True,
        )
        skill_table.add_column("s", width=36, overflow="fold")
        for skill in skills.skills:
            cell = Text()
            cell.append("• ", style="magenta")
            cell.append(skill.name, style="bold")
            cell.append("\n")
            for line in _wrap_display(skill.description, 36):
                cell.append("  ")
                cell.append(line + "\n", style="dim")
            skill_table.add_row(cell)

        schemas = self.service.registry.schemas()
        table = Table(
            show_header=False, box=None, show_edge=False,
            padding=(0, 0), pad_edge=False, collapse_padding=True,
        )
        table.add_column("t", width=36, overflow="fold")
        for spec in schemas:
            fn = spec["function"]
            cell = Text()
            cell.append("• ", style="cyan")
            cell.append(fn["name"], style="bold")
            cell.append("\n")
            for line in _wrap_display(fn["description"], 36):
                cell.append("  ")
                cell.append(line + "\n", style="dim")
            table.add_row(cell)

        skill_body = (
            skill_table
            if skills.skills
            else Text("（暂无技能，按 F2 打开市场）", style="dim")
        )

        mcp_rows = self.service.describe().get("mcp") or []
        loading = self.service.mcp_loading
        blocks = [
            Text(f"技能 · {len(skills)}  （F2 市场）\n", style=f"bold {_HEADING}"),
            skill_body,
            Text(f"\n工具 · {len(schemas)}\n", style=f"bold {_HEADING}"),
            table,
        ]

        # MCP 区域：显示每个 Server 的连接状态与它提供的工具清单
        if loading:
            blocks.extend([
                Text("\nMCP Server · 加载中…\n", style=f"bold {_HEADING}"),
                Text("后台异步连接中，完成后自动刷新本区域\n", style="dim"),
            ])
        elif mcp_rows:
            mcp_table = Table(
                show_header=False, box=None, show_edge=False,
                padding=(0, 0), pad_edge=False, collapse_padding=True,
            )
            mcp_table.add_column("m", width=36, overflow="fold")
            for row in mcp_rows:
                cell = Text()
                tools = list(row.get("tools") or [])
                if row.get("ok"):
                    cell.append("✓ ", style="green")
                    cell.append(row["name"], style="bold")
                    cell.append(f"  {len(tools)} 个工具\n")
                    for line in _wrap_display(
                        f"{row.get('type', '')} · {row.get('target', '')}", 36
                    ):
                        cell.append("  " + line + "\n", style="dim")
                    for name in tools:
                        cell.append(f"  • {name}\n", style="cyan")
                else:
                    cell.append("✗ ", style="red")
                    cell.append(row.get("name", ""), style="bold")
                    cell.append("\n")
                    for line in _wrap_display(
                        f"连接失败：{row.get('error', '')}", 36
                    ):
                        cell.append("  " + line + "\n", style="red")
                mcp_table.add_row(cell)
            ok_count = sum(1 for row in mcp_rows if row.get("ok"))
            blocks.extend([
                Text(
                    f"\nMCP Server · {ok_count}/{len(mcp_rows)} 已连接\n",
                    style=f"bold {_HEADING}",
                ),
                mcp_table,
            ])
        else:
            blocks.extend([
                Text("\nMCP Server · 0\n", style=f"bold {_HEADING}"),
                Text("（未配置 .mcp.json 或没有可连接的 Server）\n", style="dim"),
            ])
        return Group(*blocks)

    def _refresh_library(self) -> None:
        """刷新右侧栏下方“技能 / 工具 / MCP”区域。"""
        try:
            self.query_one("#tools-list", Static).update(self._build_library_text())
        except Exception:  # pragma: no cover - 界面关闭竞态
            pass
        try:
            self.query_one("#banner", Static).update(self._banner_text())
        except Exception:  # pragma: no cover - 界面关闭竞态
            pass

    def _on_mcp_ready(self) -> None:
        """MCP 后台连接完成：刷新横幅 / 侧栏 / 状态栏，并在对话区给出摘要。"""
        self._refresh_library()
        self._refresh_sidebar()
        self._refresh_status()
        statuses = self.service.mcp_statuses()
        if not statuses:
            self._notice("未发现可用的 MCP Server 配置（.mcp.json）。", label="MCP")
            return
        ok = [item for item in statuses if item.ok]
        tool_total = sum(len(item.tools) for item in ok)
        failed = [item for item in statuses if not item.ok]
        summary = (
            f"MCP 加载完成：{len(ok)}/{len(statuses)} 个 Server 已连接，"
            f"新增 {tool_total} 个工具。"
        )
        if failed:
            summary += "\n失败：" + "、".join(item.name for item in failed)
        summary += "\n详见右侧栏 MCP 区域，或输入 /mcp 查看详情。"
        self._notice(summary, label="MCP")
        self.notify(f"MCP 已加载（{len(ok)}/{len(statuses)}）", title="MCP")

    # ------------------------------------------------------------------ 快捷键
    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_market(self, query: str = "") -> None:
        """F2 / /market：打开本地技能市场。"""
        if self._worker is not None and not self._worker.is_finished:
            self.notify("生成过程中暂不能打开市场", severity="warning")
            return
        self.push_screen(
            MarketScreen(
                self.service,
                on_change=self._on_market_change,
                initial_query=query,
            )
        )

    def _on_market_change(self) -> None:
        """市场安装 / 卸载后同步当前会话与侧栏。"""
        self.session.update_system_prompt(self.service.system_prompt)
        self._refresh_library()
        self._refresh_sidebar()

    def action_clear_screen(self) -> None:
        """Ctrl+L / /clear：只清界面，保留会话历史。"""
        self._reset_transcript()
        self._stream_block = None
        self._stream_text = ""
        self._notice(f"界面已清空（历史保留，{self.session.message_count} 条）。",
                     label="命令")

    # ------------------------------------------------------------ 会话管理命令
    def _ensure_record(self, title: str = "") -> Optional[str]:
        """把当前会话写入 ``.zagent/sessions``（必要时新建记录）并绑定。"""
        if self.session.conversation_count == 0:
            return None
        record = self.service.save_session(
            self.session.history,
            record_id=self._record_id,
            title=title,
        )
        self._record_id = record.id
        # 绑定持久化：之后每轮结束（session.persist）自动写回
        self.session.attach_store(self.service.sessions, record.id)
        return record.id

    def action_save_session(self, title: str = "") -> None:
        """Ctrl+S / ``/save [标题]``：保存当前会话。"""
        if self.session.conversation_count == 0:
            self.notify("当前会话为空，无需保存", severity="warning")
            return
        record_id = self._ensure_record(title)
        self._refresh_sidebar()
        self._notice(
            f"✔ 已保存会话 {record_id} -> {self.service.sessions.directory}",
            label="会话",
        )
        self.notify(f"已保存会话 {record_id}", title="会话")

    def action_resume(self) -> None:
        """Ctrl+R / ``/resume``：历史会话面板（↑/↓ 选择、Enter 恢复、Esc 取消）。"""
        if self._worker is not None and not self._worker.is_finished:
            self.notify("生成过程中暂不能恢复会话", severity="warning")
            return
        records = self.service.list_sessions()
        if not records:
            self.notify("暂无已保存会话，先用 /save 保存当前会话", severity="warning")
            return
        self.push_screen(ResumeScreen(records), self._on_resume_selected)

    def _on_resume_selected(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        # 恢复前先保存当前会话（避免切换时丢失未保存内容）
        if self._record_id != session_id and self.session.conversation_count > 0:
            self._ensure_record()
        session = self.service.open_session(session_id)
        if session is None:
            self.notify("会话不存在或已被删除", severity="error")
            return
        self.session = session
        self._record_id = session_id
        self._turn_no = 0
        self._turn_tools = 0
        self._last_duration = 0.0
        self._stream_block = None
        self._stream_text = ""
        self._tool_cards.clear()
        self._reset_transcript()
        self._render_history(session.history)
        self._set_state("就绪")
        record = self.service.get_session(session_id)
        title = record.title if record is not None else session_id
        self._notice(
            f"✔ 已恢复会话「{title}」（{session.message_count} 条消息）",
            label="会话",
        )
        self.notify(f"已恢复会话 {session_id}", title="会话")

    def _render_history(self, messages) -> None:
        """把恢复的历史渲染回对话区（工具消息不展开，保持界面简洁）。"""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if role == "system" or _is_agents_message(message):
                continue
            if role == "user" and content:
                self._append_block(ChatBlock("你", content, kind="user"))
            elif role == "assistant" and content:
                self._append_block(ChatBlock("Zagent", content, kind="assistant"))
        if self.service.agents_md:
            self._notice("已注入 AGENTS.md 项目规范（本会话全程复用）", label="规范")
        self._scroll_end()

    def action_export(self, target: str = "") -> None:
        """``/export [目录]``：把当前会话导出为 Markdown 文件。"""
        if self.session.conversation_count == 0:
            self.notify("当前会话为空，无需导出", severity="warning")
            return
        title = ""
        if self._record_id:
            record = self.service.get_session(self._record_id)
            if record is not None:
                title = record.title
        try:
            if target.strip():
                directory = Path(target.strip()).expanduser()
                if not directory.is_absolute():
                    directory = Path(os.getcwd()) / directory
            else:
                directory = self.service.project_root / ".zagent" / "exports"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"session-{self._record_id or self._session_id}-{stamp}.md"
            path = directory / name
            path.write_text(
                history_to_markdown(
                    self.session.history,
                    title=title,
                    model=self.service.settings.model_name,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self.notify(f"导出失败：{exc}", severity="error", title="导出")
            return
        self._notice(f"✔ 已导出会话 -> {path}", label="导出")
        self.notify(f"已导出 {path.name}", title="导出")

    def action_new_session(self) -> None:
        """Ctrl+N / ``/new``：新建会话（自动保存当前会话历史）。"""
        record_id = (
            self._ensure_record()
            if self.session.conversation_count > 0
            else None
        )
        self.session = self.service.new_session()
        self._session_id = secrets.token_hex(4)
        self._record_id = None
        self._turn_no = 0
        self._turn_tools = 0
        self._last_duration = 0.0
        self._stream_block = None
        self._stream_text = ""
        self._tool_cards.clear()
        self._reset_transcript()
        self._set_state("就绪")
        if record_id:
            self._notice(
                f"已自动保存上一会话（{record_id}），并新建空会话。",
                label="会话",
            )
            self.notify(f"已新建会话（上一会话已保存 {record_id}）", title="会话")
        else:
            self.notify("已新建会话（历史为空，无需保存）", title="会话")

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.display = not sidebar.display

    def action_interrupt(self) -> None:
        """Esc：优先关闭命令补全面板；生成中则中断生成。"""
        if self._suggestions and (
            self._worker is None or self._worker.is_finished
        ):
            self._hide_suggestions()
            return
        if self._worker is not None and not self._worker.is_finished:
            self._worker.cancel()
            self._set_state("已中断")
            self._notice("已中断本轮生成。", label="命令")


class TUIFrontend(Frontend):
    """Textual 终端界面后端。"""

    name = "tui"
    description = "Textual 终端 UI（消息块 / 工具卡片 / 侧栏 / 帮助弹窗）"

    def run(self, service: AgentService) -> int:
        ZagentApp(service).run()
        return 0


def _wrap_display(text: str, width: int) -> list:
    """按显示宽度（CJK 算 2 列）折行。"""
    lines, current, used = [], "", 0
    for char in text:
        step = 2 if ord(char) > 0x2E7F else 1
        if used + step > width:
            lines.append(current)
            current, used = "", 0
        current += char
        used += step
    if current:
        lines.append(current)
    return lines or [""]


__all__ = ["TUIFrontend", "ZagentApp"]
