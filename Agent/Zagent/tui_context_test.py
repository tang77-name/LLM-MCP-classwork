# -*- coding: utf-8 -*-
"""TUI 上下文监控冒烟测试（textual headless，不调用真实 LLM）。

验证需求 8 与输入拦截：

- 状态栏醒目位置显示 ``上下文 k / 窗口 k(默认) · 百分比``；
- 侧栏同步显示上下文占用；
- 达到 80% 后普通输入被拦截并给出 /compact 提示；
- ``/compact`` 命令正确路由到压缩流程（空历史时直接返回“无需压缩”）。

用法::

    python tui_context_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import AgentService, load_settings  # noqa: E402
from tui.app import ZagentApp  # noqa: E402

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


class _SubmitEvent:
    """伪造 Input.Submitted（直接调用处理器，避免依赖事件构造细节）。"""

    def __init__(self, widget, value: str) -> None:
        self.input = widget
        self.value = value


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


async def wait_until(pilot, cond, timeout: float = 20.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if cond():
            return True
        await pilot.pause(0.1)
    return bool(cond())


async def run() -> int:
    service = AgentService(load_settings())
    app = ZagentApp(service)
    try:
        async with app.run_test(size=(140, 40)) as pilot:
            # 拦截 _notice，直接断言对话区提示（避免依赖渲染结果）
            notices: list = []
            original_notice = app._notice

            def spy(text: str, label: str = "提示") -> None:
                notices.append(f"[{label}] {text}")
                original_notice(text, label)

            app._notice = spy  # type: ignore[assignment]

            # 1) 状态栏显示上下文占用（默认 128k 标注）
            await pilot.pause(0.2)
            status = render_widget(app.query_one("#status"))
            check("状态栏显示上下文", "上下文" in status, status)
            check("状态栏显示窗口大小与百分比",
                  "128.0k" in status and "%" in status, status)
            check("未知窗口标注“默认”", "默认" in status, status)

            # 2) 侧栏同步显示（Static 的渲染结果是 textual 包装对象，
            #    直接拦截 update 拿到 rich Group 再渲染成文本）
            sidebar_widget = app.query_one("#session-info")
            payloads: list = []
            original_update = sidebar_widget.update

            def spy_update(renderable=None, *args, **kwargs):
                payloads.append(renderable)
                return original_update(renderable, *args, **kwargs)

            sidebar_widget.update = spy_update  # type: ignore[assignment]
            app._refresh_sidebar()
            sidebar = render_widget(payloads[-1]) if payloads else ""
            check("侧栏显示上下文占用", "上下文" in sidebar, sidebar[:300])

            # 3) 达到 80% 后拦截普通输入
            before = app.session.message_count
            app.session._monitor.blocked = True
            prompt = app.query_one("#prompt")
            app._on_submit(_SubmitEvent(prompt, "这是一条应该被拦截的提问"))
            await pilot.pause(0.3)
            text = "".join(notices)
            check("拦截提示出现在对话区", "上下文占用已达" in text, text[-400:])
            check("拦截时不启动生成", app._worker is None,
                  str(app._worker))
            check("拦截时不追加消息", app.session.message_count == before,
                  f"{app.session.message_count} vs {before}")
            check("输入框保持可用（仍可输入 /compact）",
                  not prompt.disabled, "disabled")

            # 4) /compact 路由到压缩流程（空历史 -> 无需压缩，不调用 LLM）
            notices.clear()
            app.session._monitor.blocked = False
            app._on_submit(_SubmitEvent(prompt, "/compact"))
            finished = await wait_until(
                pilot,
                lambda: app._worker is not None and app._worker.is_finished,
            )
            await pilot.pause(0.3)
            text = "".join(notices)
            check("/compact 被路由到压缩流程", finished, "worker 未启动/未结束")
            check("空历史给出“无需压缩”", "无需压缩" in text, text[-400:])
            check("压缩后状态回到就绪", app._state == "就绪", app._state)
            check("压缩后输入框恢复", not prompt.disabled, "disabled")
            check("压缩后未进入 blocked", not app.session.blocked)
    finally:
        service.shutdown()

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
