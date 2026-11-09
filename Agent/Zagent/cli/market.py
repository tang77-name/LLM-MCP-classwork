# -*- coding: utf-8 -*-
"""CLI 本地技能市场：列表 / 搜索 / 详情 / 安装 / 卸载。

展示逻辑属于 CLI 层；业务由 :class:`core.market.SkillMarket` 完成。
"""

from __future__ import annotations

import shlex

from core.agent import AgentService, AgentSession
from core.market import MarketListing, MarketPackage


def _short(text: str, limit: int = 100) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit] + "…"


def print_listing(listing: MarketListing, query: str = "") -> None:
    title = "搜索结果" if query else "市场技能"
    print(f"\n{title} · {len(listing.packages)} 个"
          f"（市场 {listing.market_count} / 已安装 {listing.installed_count}）",
          flush=True)
    print(f"  市场目录: {listing.market_dir}", flush=True)
    print(f"  安装目录: {listing.skills_dir}", flush=True)

    if not listing.packages:
        print("  （没有匹配的技能）", flush=True)
        if not query:
            print(
                "  提示：把技能包放到 market/<名称>/skill.md，"
                "或设置 [agent] market_dir。",
                flush=True,
            )
    else:
        print(flush=True)
        for package in listing.packages:
            state = "已安装  " if package.installed else "  可安装"
            version = package.version or "-"
            origin = "" if package.in_market else "  本地已安装"
            print(
                f"  {state}  {package.name:<24} v{version:<8} "
                f"[{package.category}]{origin}",
                flush=True,
            )
            print(f"           {_short(package.description)}", flush=True)

    for warning in listing.warnings:
        print(f"  ! {warning}", flush=True)


def print_detail(package: MarketPackage) -> None:
    print(f"\n技能 · {package.name}", flush=True)
    print(f"  状态: {package.status}", flush=True)
    print(f"  类型: {package.source_type}", flush=True)
    print(f"  版本: {package.version or '-'}", flush=True)
    print(f"  分类: {package.category}", flush=True)
    print(f"  作者: {package.author or '-'}", flush=True)
    print(f"  标签: {', '.join(package.tags) or '-'}", flush=True)
    if package.homepage:
        print(f"  主页: {package.homepage}", flush=True)
    print(f"  来源: {package.directory}", flush=True)
    if package.installed_directory:
        print(f"  已安装到: {package.installed_directory}", flush=True)
    print("  说明:", flush=True)
    for line in (package.description or "").splitlines() or [""]:
        print(f"    {line}", flush=True)
    files = package.files()
    print(f"  文件 ({len(files)}):", flush=True)
    for name in files:
        print(f"    - {name}", flush=True)


def _print_help() -> None:
    print(
        "\n市场命令:\n"
        "  list / ls              列出全部技能\n"
        "  search <关键词>        搜索名称、描述、分类、标签\n"
        "  info <名称>            查看技能详情与文件\n"
        "  install <名称> [--force]  安装（--force 覆盖已安装）\n"
        "  uninstall <名称>       卸载已安装技能\n"
        "  refresh                重新扫描市场\n"
        "  help                   显示本帮助\n"
        "  back / q / quit        返回对话\n",
        flush=True,
    )


def _dispatch(
    service: AgentService,
    session: AgentSession,
    raw: str,
) -> None:
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        print(f"错误：命令格式非法 -> {exc}", flush=True)
        return
    if not parts:
        return

    action = parts[0].casefold()
    args = parts[1:]

    if action in ("list", "ls"):
        print_listing(service.market.search(""))
    elif action == "search":
        query = " ".join(args).strip()
        if not query:
            print("用法：search <关键词>", flush=True)
            return
        print_listing(service.market.search(query), query)
    elif action == "info":
        if not args:
            print("用法：info <名称>", flush=True)
            return
        package = service.market.get(args[0])
        if package is None:
            print(f"错误：市场中没有找到技能 {args[0]!r}", flush=True)
            return
        print_detail(package)
    elif action == "install":
        if not args:
            print("用法：install <名称> [--force]", flush=True)
            return
        name = args[0]
        force = "--force" in args[1:] or "-f" in args[1:]
        result = service.install_market_skill(name, force=force)
        session.update_system_prompt(service.system_prompt)
        suffix = "（已覆盖旧版本）" if result.replaced else ""
        print(
            f"已安装技能 {result.name} v{result.version or '-'}{suffix}\n"
            f"  -> {result.target}\n"
            f"  当前会话的 skill 清单已刷新。",
            flush=True,
        )
    elif action in ("uninstall", "remove"):
        if not args:
            print("用法：uninstall <名称>", flush=True)
            return
        result = service.uninstall_skill(args[0])
        session.update_system_prompt(service.system_prompt)
        print(
            f"已卸载技能 {result.name}\n  -> {result.removed}\n"
            f"  当前会话的 skill 清单已刷新。",
            flush=True,
        )
    elif action in ("refresh", "reload"):
        print_listing(service.market.search(""))
    elif action in ("help", "?", "h"):
        _print_help()
    else:
        print(f"未知市场命令 {action!r}；输入 help 查看用法。", flush=True)


def run_market(
    service: AgentService,
    session: AgentSession,
    initial: str = "",
) -> None:
    """运行本地技能市场；``initial`` 非空时执行一次命令后返回。"""
    listing = service.market.search("")
    print("=" * 64, flush=True)
    print(" Zagent Market · 本地技能市场", flush=True)
    print(f" 市场目录: {listing.market_dir}", flush=True)
    print(f" 安装目录: {listing.skills_dir}", flush=True)
    print(f" 市场包: {listing.market_count} 个 | 已安装: {listing.installed_count}", flush=True)
    print(" 输入 help 查看市场命令，back / q 返回对话", flush=True)
    print("=" * 64, flush=True)

    if initial.strip():
        _dispatch(service, session, initial.strip())
        return

    while True:
        try:
            raw = input("\nmarket> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            return
        if not raw:
            continue
        if raw.casefold() in ("back", "q", "quit", "exit"):
            return
        _dispatch(service, session, raw)


__all__ = ["run_market", "print_listing", "print_detail"]
