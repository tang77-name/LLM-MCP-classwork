# -*- coding: utf-8 -*-
"""TUI 新功能冒烟测试（textual headless，不调用真实 LLM）。

覆盖：

1. 首次运行生成 config.ini 后的提示对话框（可关闭并继续）；
2. 输入 "/" 弹出斜杠命令补全面板：↑/↓ 选择、Enter 补全、完整命令直接执行；
3. ``/save`` 保存、``/new`` 自动保存、``/resume`` 面板恢复（回车选中）；
4. 侧栏 MCP 区域（无 .mcp.json 时显示 0，不再空白）；
5. AGENTS.md 注入到会话历史。

用法::

    python tui_extras_test.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from textual.containers import Vertical  # noqa: E402
from textual.widgets import Button, Input, OptionList  # noqa: E402

from core import AgentService, load_settings  # noqa: E402
from tui.app import ZagentApp  # noqa: E402
from tui.help import HelpScreen  # noqa: E402
from tui.resume import ResumeScreen  # noqa: E402
from tui.startup import StartupScreen  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def render_widget(widget) -> str:
    """把 rich 渲染对象 / Textual 控件转成纯文本。"""
    import io

    from rich.console import Console

    obj = getattr(widget, "_renderable", None)
    if obj is None and hasattr(widget, "render"):
        obj = widget.render()
    if obj is None:
        obj = widget
    buffer = io.StringIO()
    Console(file=buffer, width=200, force_terminal=False).print(obj)
    return buffer.getvalue()


async def run() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="zagent-tui-"))
    previous = Path.cwd()
    os.chdir(workdir)
    # 项目规范：验证“一次注入”与侧栏标记
    (workdir / "AGENTS.md").write_text("# 项目规范\n- 回答保持简洁\n", encoding="utf-8")

    try:
        settings = load_settings()  # 首次运行 -> 自动生成 config.ini + notice
        # TUI 使用异步 MCP：先进入界面，连接完成后动态刷新侧栏
        service = AgentService(settings, mcp_async=True)
        app = ZagentApp(service)
        try:
            async with app.run_test(size=(150, 45)) as pilot:
                await pilot.pause(0.4)

                # 1) 首次运行提示对话框
                check("首次运行弹出配置提示", isinstance(app.screen, StartupScreen),
                      type(app.screen).__name__)
                notice = getattr(app.screen, "message", "")
                check("提示包含模拟信息 / 重新启动",
                      "模拟信息" in notice and "重新启动" in notice, notice[:80])
                app.screen.query_one("#startup-continue", Button).focus()
                await pilot.press("enter")
                await pilot.pause(0.3)
                check("选择继续后关闭弹窗", not isinstance(app.screen, StartupScreen))
                check("关闭弹窗后应用仍在运行", app.is_running)

                prompt = app.query_one("#prompt", Input)
                prompt.focus()
                await pilot.pause(0.1)

                # 5) AGENTS.md 已注入
                check("会话注入 AGENTS.md", app.session.agents_md is not None)
                check(
                    "历史第二条为规范消息",
                    app.session.history[1]["content"].startswith("[项目规范 AGENTS.md]"),
                    str(app.session.history[0])[:60],
                )

                # 2) 斜杠命令补全面板
                prompt.value = "/co"
                await pilot.pause(0.2)
                panel = app.query_one("#cmd-panel", Vertical)
                options = app.query_one("#cmd-options", OptionList)
                check("输入 /co 弹出面板", bool(panel.display), str(panel.display))
                check("面板列出 compact/config", options.option_count == 2,
                      str(options.option_count))
                await pilot.press("down")
                await pilot.pause(0.15)
                check("↓ 切换到第二项", options.highlighted == 1,
                      str(options.highlighted))
                await pilot.press("up")
                await pilot.pause(0.15)
                check("↑ 回到第一项", options.highlighted == 0,
                      str(options.highlighted))
                await pilot.press("enter")
                await pilot.pause(0.25)
                check("Enter 补全为 /compact", prompt.value.strip() == "/compact",
                      repr(prompt.value))
                check("补全后收起面板", not panel.display)

                # 完整命令：Enter 直接执行（不重复补全）
                prompt.value = "/help"
                await pilot.pause(0.15)
                check("完整命令仍显示面板", bool(panel.display))
                await pilot.press("enter")
                await pilot.pause(0.4)
                check("完整命令直接执行", isinstance(app.screen, HelpScreen),
                      type(app.screen).__name__)
                await pilot.press("escape")
                await pilot.pause(0.3)
                check("帮助关闭后回到主界面", not isinstance(app.screen, HelpScreen))

                # 输入含空格或非斜杠文本时不弹面板
                prompt.value = "/compact now"
                await pilot.pause(0.15)
                check("含空格时面板收起", not panel.display)
                prompt.value = "普通问题"
                await pilot.pause(0.15)
                check("普通文本不弹面板", not panel.display)

                # 3) /save -> /new 自动保存 -> /resume 恢复
                app.session.load_history([
                    {"role": "user", "content": "第一条问题"},
                    {"role": "assistant", "content": "第一条回答"},
                ])
                prompt.value = "/save 测试会话"
                await pilot.press("enter")
                await pilot.pause(0.4)
                records = service.list_sessions()
                check("/save 创建会话记录", bool(app._record_id), str(app._record_id))
                check("记录标题来自参数", records and records[0].title == "测试会话",
                      str([r.title for r in records]))
                saved_id = app._record_id

                app.action_new_session()
                await pilot.pause(0.3)
                check("/new 后对话历史清空", app.session.conversation_count == 0,
                      str(app.session.message_count))
                check("/new 自动保存旧会话",
                      bool(service.get_session(saved_id or "")),
                      str(saved_id))
                saved_record = service.get_session(saved_id or "")
                check(
                    "自动保存包含完整消息（含 AGENTS.md 规范）",
                    saved_record is not None and saved_record.message_count == 3
                    and any(
                        str(m.get("content", "")).startswith("[项目规范 AGENTS.md]")
                        for m in saved_record.messages
                    ),
                    str(saved_record.message_count if saved_record else None),
                )
                check("/new 后解除记录绑定", app._record_id is None)

                prompt = app.query_one("#prompt", Input)
                prompt.value = "/resume"
                await pilot.press("enter")
                await pilot.pause(0.4)
                check("打开历史会话面板", isinstance(app.screen, ResumeScreen),
                      type(app.screen).__name__)
                await pilot.press("enter")
                await pilot.pause(0.5)
                check("回车恢复会话", app._record_id == saved_id, str(app._record_id))
                check("恢复后历史包含旧消息",
                      any(m.get("content") == "第一条回答" for m in app.session.history),
                      str(app.session.message_count))

                # 4) 侧栏 MCP 区域（异步加载完成后自动刷新）
                for _ in range(40):
                    if not service.mcp_loading:
                        break
                    await pilot.pause(0.2)
                check("后台 MCP 加载结束", not service.mcp_loading)
                library = render_widget(app._build_library_text())
                check("侧栏包含 MCP 区域", "MCP Server" in library, library[:200])
                check("侧栏标注未配置 Server", "未配置" in library, library[-200:])
        finally:
            service.shutdown()
    finally:
        os.chdir(previous)
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
