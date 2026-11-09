#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Zagent 命令行入口（唯一的进程级装配点）。

职责：

1. 解析命令行参数（前端选择、配置覆盖）；
2. 装配 core（``AgentService``，组合根）；
3. 通过 :mod:`frontends`（应用外壳）实例化前端并运行。

依赖方向::

    main.py -> frontends.py -> {cli, tui, ...} -> core
    core 不依赖 main / frontends / 任何前端
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

import frontends
from core import AgentService, __version__, load_settings
from core.config import with_overrides
from core.errors import ConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zagent",
        description="Zagent · 可插拔前端的流式工具调用 Agent（架构图 / 日期 / 搜索 / 网页抓取 / 技能市场）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python main.py                 # 自动选择前端（tui 优先）\n"
            "  python main.py --ui tui\n"
            "  python main.py --ui cli --model deepseek-chat\n"
            "  python main.py --ui auto --diagram-dir ./out/diagrams\n"
            "  python main.py --ui tui --market-dir ./market\n"
            "  python main.py --ui web --web-port 8787\n"
        ),
    )
    parser.add_argument(
        "--ui",
        default="auto",
        help="前端：auto（默认，tui 优先、缺失时回退 cli）| tui | cli | ...（--list-ui 查看）",
    )
    parser.add_argument("--config", help="指定配置文件路径（默认自动探测 config.ini）")
    parser.add_argument("--model", help="覆盖模型名（CHAT_MODEL）")
    parser.add_argument("--base-url", help="覆盖接口地址（OPENAI_BASE_URL）")
    parser.add_argument("--diagram-dir", help="覆盖架构图默认输出目录")
    parser.add_argument("--market-dir", help="覆盖本地技能市场目录")
    parser.add_argument("--web-host", help="Web 前端监听地址（默认 127.0.0.1）")
    parser.add_argument("--web-port", type=int, help="Web 前端监听端口（默认 8787）")
    parser.add_argument(
        "--no-web-browser",
        action="store_true",
        help="启动 Web 前端时不自动打开浏览器",
    )
    parser.add_argument("--temperature", type=float, help="覆盖采样温度 0~2")
    parser.add_argument("--list-ui", action="store_true", help="列出可用前端后退出")
    parser.add_argument("--version", action="version", version=f"zagent {__version__}")
    return parser


def resolve_frontend_name(requested: str) -> str:
    """解析 ``--ui`` 参数为可用前端标识。"""
    if requested != "auto":
        if requested in frontends.available_names():
            return requested
        reason = frontends.unavailable().get(requested)
        hint = f"（{reason}）" if reason else (
            f"，可用: {', '.join(frontends.available_names()) or '（无）'}"
        )
        raise SystemExit(f"错误：前端 {requested!r} 不可用{hint}")

    name = frontends.default_name()
    if not name:
        raise SystemExit("错误：没有可用的前端")
    return name


def list_frontends() -> int:
    for name, cls in sorted(frontends.available().items()):
        print(f"  {name:<8} {cls.description}")
    for pkg, reason in frontends.unavailable().items():
        print(f"  {pkg:<8} 不可用: {reason}")
    return 0


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_ui:
        return list_frontends()

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    overrides = {}
    if args.model:
        overrides["model_name"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url.rstrip("/")
    if args.diagram_dir:
        overrides["diagram_dir"] = args.diagram_dir
    if args.market_dir:
        overrides["market_dir"] = args.market_dir
    if args.web_host:
        overrides["web_host"] = args.web_host
    if args.web_port is not None:
        if args.web_port < 0 or args.web_port > 65535:
            print("错误：--web-port 需在 [0, 65535] 区间", file=sys.stderr)
            return 2
        overrides["web_port"] = args.web_port
    if args.no_web_browser:
        overrides["web_open_browser"] = False
    if args.temperature is not None:
        overrides["temperature"] = args.temperature
    if overrides:
        settings = with_overrides(settings, **overrides)

    # 先确定前端：TUI 需要“先进入界面、MCP 后台异步加载并动态刷新”
    try:
        frontend_name = resolve_frontend_name(args.ui)
        frontend = frontends.create(frontend_name)
    except KeyError as exc:
        print(f"错误: {exc.args[0]}", file=sys.stderr)
        return 2

    service = AgentService(settings, mcp_async=(frontend_name == "tui"))

    # 首次运行自动生成了 config.ini：TUI / CLI 会各自弹窗或询问，
    # 其余前端（Web 等）在控制台给出提示。
    if settings.startup_notice and frontend_name not in ("tui", "cli"):
        print(f"提示：{settings.startup_notice}", file=sys.stderr)

    try:
        return int(frontend.run(service) or 0)
    finally:
        # 释放 MCP 长连接与 stdio 子进程，避免残留进程
        service.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
