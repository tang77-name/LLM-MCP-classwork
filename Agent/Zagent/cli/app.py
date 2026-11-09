# -*- coding: utf-8 -*-
"""CLI 前端（标准输入输出，无第三方依赖的参考实现 / 回退界面）。

职责仅有一件事：把 :class:`core.events.Event` 渲染到标准输出，
并把用户输入转交给 :class:`core.agent.session.AgentSession`。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.agent import AgentService, AgentSession
from core.commands import Command, command_rows, parse
from core.events import (
    AssistantText,
    CompactEnd,
    CompactProgress,
    CompactStart,
    ContextUsage,
    Event,
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

from .market import run_market
from .render import short_json, short_text

#: 与 TUI 帮助弹窗一致的快捷键/用法说明（CLI 版）
_CLI_KEYS = (
    "Enter 发送 | exit/quit/q 退出 | /help 帮助 | /clear 清屏 | /new 新会话（自动保存） | "
    "/save 保存 | /resume 恢复 | /export 导出 | /compact 压缩 | /tools 工具 | "
    "/mcp 服务器 | /config 配置 | /cwd 目录 | /model 模型"
)


class CLIFrontend(Frontend):
    """命令行界面。"""

    name = "cli"
    description = "终端逐字流式聊天（标准输入输出，无第三方依赖）"

    def run(self, service: AgentService) -> int:
        info = service.describe()
        session = service.new_session()
        record_id: Optional[str] = None

        print("=" * 60)
        print(" Zagent · 流式多轮聊天（架构图 + 日期 + 联网搜索 + 网页抓取）")
        print(f" 模型: {info['model_name']} | 接口: {info['base_url']}")
        print(f" 架构图保存目录: {info['diagram_dir']}")
        print(f" 工具: {', '.join(info['tools'])}")
        print(f" 技能: {len(info['skills'])} 个（/market 浏览本地技能市场）")
        self._show_mcp(info)
        print(" 输入 /help 查看命令，exit / quit / q 退出")
        print("=" * 60)

        # 首次运行自动生成 config.ini：先提示用户修改后再重启
        if service.settings.startup_notice:
            if not self._confirm_startup(service):
                return 0

        while True:
            try:
                question = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出。")
                return 0

            if not question:
                continue

            command, argument = parse(question)
            if command is Command.EXIT:
                print("已退出。")
                return 0
            if command is Command.HELP:
                self._show_help()
                continue
            if command is Command.CLEAR:
                # 清空界面（保留历史）
                print("\033[2J\033[H", end="", flush=True)
                print(f"(界面已清空，历史保留 {session.message_count} 条)",
                      flush=True)
                continue
            if command is Command.NEW:
                if session.conversation_count > 0:
                    record_id = self._save(service, session, record_id)
                session = service.new_session()
                record_id = None
                print("已新建会话（历史已重置）。", flush=True)
                continue
            if command is Command.SAVE:
                record_id = self._save(service, session, record_id, argument)
                continue
            if command is Command.RESUME:
                session, record_id = self._resume(service, session, record_id)
                continue
            if command is Command.EXPORT:
                self._export(service, session, record_id, argument)
                continue
            if command is Command.COMPACT:
                for event in session.compact():
                    self._render(event)
                continue
            if command is Command.TOOLS:
                self._show_tools(service)
                continue
            if command is Command.MCP:
                self._show_mcp_detail(service)
                continue
            if command is Command.MARKET:
                run_market(service, session, argument)
                continue
            if command is Command.CWD:
                print(os.getcwd(), flush=True)
                continue
            if command is Command.MODEL:
                cfg = service.settings.masked()
                print(f"模型 {cfg['model_name']} | 接口 {cfg['base_url']}",
                      flush=True)
                continue
            if command is Command.CONFIG:
                self._show_config(service)
                continue

            # 上下文已达 80% 阈值：暂停普通输入，直到 /compact 成功
            if session.blocked:
                usage = session.context_usage
                print(
                    f"[上下文已满] 占用 {usage.summary_text}，"
                    "已暂停接收新提示词；请输入 /compact 压缩后继续。",
                    file=sys.stderr, flush=True,
                )
                continue

            for event in session.ask(question):
                self._render(event)
            usage = session.context_usage
            print(f"[上下文] {usage.summary_text}", file=sys.stderr, flush=True)

    # -- 启动提示 / 会话管理 ------------------------------------------------
    def _confirm_startup(self, service: AgentService) -> bool:
        """首次生成 config.ini 后的确认提示（返回 False 表示退出）。"""
        print("\n" + "=" * 60)
        print(" 首次运行提示")
        print("-" * 60)
        for line in service.settings.startup_notice.splitlines():
            print(" " + line)
        print("=" * 60)
        try:
            answer = input(
                "修改完配置后请重新启动。按 Enter 仍然继续，输入 q 退出: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            answer = "q"
        if answer.casefold() in ("q", "quit", "exit"):
            print("已退出，请修改 config.ini 后重新启动。")
            return False
        print()
        return True

    def _save(
        self,
        service: AgentService,
        session: AgentSession,
        record_id: Optional[str],
        title: str = "",
    ) -> Optional[str]:
        """保存当前会话到 ``.zagent/sessions``（必要时新建记录）。"""
        if session.conversation_count == 0:
            print("当前会话为空，无需保存。", flush=True)
            return record_id
        record = service.save_session(
            session.history, record_id=record_id, title=title
        )
        session.attach_store(service.sessions, record.id)
        print(
            f"已保存会话 {record.id}（{record.message_count} 条消息）"
            f" -> {service.sessions.directory}",
            flush=True,
        )
        return record.id

    def _resume(
        self,
        service: AgentService,
        session: AgentSession,
        record_id: Optional[str],
    ):
        """列出历史会话，按序号恢复（回车取消）。"""
        records = service.list_sessions()
        if not records:
            print("暂无已保存会话，先用 /save 保存当前会话。", flush=True)
            return session, record_id
        print(f"\n历史会话 · {len(records)} 条", flush=True)
        for index, record in enumerate(records, 1):
            print(
                f"  {index:>2}. {record.title or '（未命名）'}"
                f" · {record.message_count} 条 · {record.id}",
                flush=True,
            )
        try:
            choice = input("输入序号恢复（直接回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return session, record_id
        if not choice:
            return session, record_id
        try:
            index = int(choice)
        except ValueError:
            print("输入的不是序号，已取消。", flush=True)
            return session, record_id
        if not 1 <= index <= len(records):
            print("序号超出范围，已取消。", flush=True)
            return session, record_id
        record = records[index - 1]
        # 恢复前先保存当前会话（避免切换时丢失未保存内容）
        if session.conversation_count > 0 and record_id != record.id:
            record_id = self._save(service, session, record_id)
        opened = service.open_session(record.id)
        if opened is None:
            print("会话不存在或已被删除。", flush=True)
            return session, record_id
        print(
            f"已恢复会话「{record.title}」（{opened.message_count} 条消息）。",
            flush=True,
        )
        return opened, opened.session_id

    def _export(
        self,
        service: AgentService,
        session: AgentSession,
        record_id: Optional[str],
        target: str = "",
    ) -> None:
        """/export [目录]：导出 Markdown。"""
        if session.conversation_count == 0:
            print("当前会话为空，无需导出。", flush=True)
            return
        try:
            if target.strip():
                directory = Path(target.strip()).expanduser()
                if not directory.is_absolute():
                    directory = Path(os.getcwd()) / directory
            else:
                directory = service.project_root / ".zagent" / "exports"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = directory / f"session-{record_id or 'memory'}-{stamp}.md"
            title = ""
            if record_id:
                record = service.get_session(record_id)
                if record is not None:
                    title = record.title
            path.write_text(
                history_to_markdown(
                    session.history,
                    title=title,
                    model=service.settings.model_name,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"导出失败：{exc}", file=sys.stderr, flush=True)
            return
        print(f"已导出会话 -> {path}", flush=True)

    def _show_mcp_detail(self, service: AgentService) -> None:
        """/mcp：MCP Server 与工具清单。"""
        rows = service.mcp_statuses()
        if not rows:
            print("未配置 MCP Server（当前目录没有 .mcp.json 或解析为空）。",
                  flush=True)
            return
        print(f"\nMCP Server · {len(rows)}", flush=True)
        for row in rows:
            if row.ok:
                print(f"  ✓ {row.name}（{row.transport}）· {len(row.tools)} 个工具",
                      flush=True)
                print(f"    地址：{row.target}", flush=True)
                for name in row.tools:
                    print(f"    • {name}", flush=True)
            else:
                print(f"  ✗ {row.name}：{row.error}", file=sys.stderr,
                      flush=True)

    def _show_config(self, service: AgentService) -> None:
        """/config：当前生效配置（脱敏）。"""
        info = service.settings.masked()
        print(f"\n配置文件：{info['config_path'] or '（内置默认值）'}", flush=True)
        for key in (
            "base_url", "api_key", "model_name", "temperature", "timeout",
            "max_tool_rounds", "context_window", "search_timeout",
            "search_top_k", "search_snippet_chars", "web_host", "web_port",
        ):
            print(f"  {key} = {info[key]}", flush=True)
        print("  工具参数：", flush=True)
        for key, value in (info.get("tool_params") or {}).items():
            print(f"    {key} = {value}", flush=True)
        print("  MCP 参数：", flush=True)
        for key, value in (info.get("mcp_params") or {}).items():
            print(f"    {key} = {value}", flush=True)

    # -- 命令输出 -----------------------------------------------------------
    def _show_mcp(self, info: dict) -> None:
        """启动横幅里的 MCP Server 状态（失败单独打到 stderr）。"""
        for row in info.get("mcp") or []:
            if row.get("ok"):
                print(
                    f" MCP: {row['name']} 已连接"
                    f"（{row.get('tool_count', 0)} 个工具，{row.get('type', '')}）"
                )
            else:
                target = row.get("target") or ""
                where = f" ({target})" if target else ""
                print(
                    f"[MCP 错误] {row.get('name')}{where}：{row.get('error')}",
                    file=sys.stderr,
                    flush=True,
                )

    def _show_help(self) -> None:
        print("\nZagent 帮助", flush=True)
        print("  使用方式：在提示符后输入问题回车发送；模型会自动调用工具。",
              flush=True)
        print("  命令：", flush=True)
        for name, summary in command_rows():
            print(f"    {name:<8} {summary}", flush=True)
        print(f"  快捷键：{_CLI_KEYS}", flush=True)

    def _show_tools(self, service: AgentService) -> None:
        schemas = service.registry.schemas()
        print(f"\n工具 · {len(schemas)}", flush=True)
        for spec in schemas:
            fn = spec["function"]
            print(f"  • {fn['name']}\n      {fn['description']}", flush=True)


    # -- 事件渲染 -----------------------------------------------------------
    def _render(self, event: Event) -> None:
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, AssistantText):
            if event.text:
                print()
        elif isinstance(event, ToolCallStart):
            print(f"[调用工具] {event.name}({short_json(event.arguments)})", flush=True)
        elif isinstance(event, ToolCallEnd):
            prefix = "" if event.ok else "错误: "
            print(f"[工具结果] {prefix}{short_text(event.result)}", flush=True)
        elif isinstance(event, LimitReached):
            print(f"[警告] 工具调用轮次达到上限（{event.rounds}），已停止本轮。",
                  file=sys.stderr, flush=True)
        elif isinstance(event, TurnError):
            print(f"[{event.category} 错误] {event.message}", file=sys.stderr,
                  flush=True)
        elif isinstance(event, HistoryCleared):
            print("历史记录已清空。", flush=True)
        elif isinstance(event, ContextUsage):
            # 只在越过阈值时提示（避免每次响应都刷屏）
            if event.warning:
                window = f"{event.window / 1024:.0f}k"
                if event.default_window:
                    window += "（默认）"
                print(
                    f"[警告] 上下文已占用 {event.percent * 100:.1f}%"
                    f"（{event.tokens / 1024:.1f}k/{window}），"
                    "请输入 /compact 压缩上下文。",
                    file=sys.stderr, flush=True,
                )
        elif isinstance(event, CompactStart):
            print("[压缩] 开始上下文压缩…", flush=True)
        elif isinstance(event, CompactProgress):
            print(f"[压缩] {event.message}", flush=True)
        elif isinstance(event, CompactEnd):
            flag = "完成" if event.ok else "失败"
            print(f"[压缩] {flag}：{event.message}", flush=True)
        elif isinstance(event, TurnEnd):
            print(f"[历史] 共 {event.message_count} 条消息", file=sys.stderr,
                  flush=True)


__all__ = ["CLIFrontend"]
