# -*- coding: utf-8 -*-
"""会话控制命令（各界面共用的交互语义与命令目录）。

- :class:`Command`        命令分类结果（界面把用户输入翻译成它）
- :data:`COMMAND_TABLE`   命令目录（名称 + 摘要），各界面的 /help 都从这里渲染
- :func:`classify`        输入 -> 命令（整串匹配）
- :func:`parse`           输入 -> (命令, 参数)（支持 ``/save 标题`` 这类带参命令）

命令语义：

==============  =========================================
/help           打开帮助
/clear          清空界面（保留历史）
/new            新建会话（自动保存当前会话历史）
/save           保存当前会话
/resume         恢复历史会话（面板选择）
/export         导出会话为 Markdown 文件
/compact        压缩上下文（保存归档并生成摘要）
/tools          列出已注册工具
/mcp            查看 MCP 服务器状态与工具
/market         浏览 / 安装本地技能市场
/cwd            显示工作目录
/model          显示模型与接口
/config         显示当前配置（脱敏）
/quit           退出（别名 exit / quit / q）
==============  =========================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Tuple


class Command(Enum):
    """用户输入的分类结果。"""

    NONE = auto()   # 普通提问
    EXIT = auto()   # 退出
    CLEAR = auto()  # 清空界面（保留历史）
    NEW = auto()    # 新建会话（自动保存当前会话）
    SAVE = auto()   # 保存当前会话
    RESUME = auto()  # 恢复历史会话
    EXPORT = auto()  # 导出会话为 Markdown
    COMPACT = auto()  # 压缩上下文（归档 + LLM 摘要）
    HELP = auto()   # 打开帮助
    TOOLS = auto()  # 列出工具
    MCP = auto()    # 查看 MCP 服务器状态
    MARKET = auto() # 本地技能市场
    CWD = auto()    # 显示工作目录
    MODEL = auto()  # 显示模型与接口
    CONFIG = auto()  # 显示当前配置


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的目录项。"""

    command: Command
    name: str                      # 主命令，例如 "/help"
    summary: str                   # 中文说明（help 表格用）
    aliases: Tuple[str, ...] = field(default_factory=tuple)


#: 命令目录（顺序即 /help 表格顺序）
COMMAND_TABLE: Tuple[CommandSpec, ...] = (
    CommandSpec(Command.HELP, "/help", "打开本帮助", ("help",)),
    CommandSpec(Command.CLEAR, "/clear", "清空界面（保留历史）", ("clear",)),
    CommandSpec(
        Command.NEW, "/new",
        "新建会话（自动保存当前会话历史）",
        ("new",),
    ),
    CommandSpec(Command.SAVE, "/save", "保存当前会话", ("save",)),
    CommandSpec(
        Command.RESUME, "/resume",
        "恢复历史会话（面板：上下键选择、回车确认、Esc 取消）",
        ("resume",),
    ),
    CommandSpec(
        Command.EXPORT, "/export",
        "导出会话为 Markdown 文件（可带保存目录参数）",
        ("export",),
    ),
    CommandSpec(
        Command.COMPACT, "/compact",
        "压缩上下文：归档原始历史并生成约 5k token 的摘要",
        ("compact",),
    ),
    CommandSpec(Command.TOOLS, "/tools", "列出已注册工具",),
    CommandSpec(Command.MCP, "/mcp", "查看 MCP 服务器状态与可用工具", ("mcp",)),
    CommandSpec(Command.MARKET, "/market", "浏览 / 安装本地技能市场", ("market",)),
    CommandSpec(Command.CWD, "/cwd", "显示工作目录",),
    CommandSpec(Command.MODEL, "/model", "显示模型与接口",),
    CommandSpec(Command.CONFIG, "/config", "显示当前配置（脱敏）", ("config",)),
    CommandSpec(
        Command.EXIT, "/quit",
        "退出",
        ("exit", "quit", "q", "/exit", "/q"),
    ),
)

_ALIAS_MAP: Dict[str, Command] = {}
for _spec in COMMAND_TABLE:
    _ALIAS_MAP[_spec.name.lower()] = _spec.command
    for _alias in _spec.aliases:
        _ALIAS_MAP[_alias.lower()] = _spec.command


def classify(text: str) -> Command:
    """把原始输入归类为 :class:`Command`（不识别的都是普通提问）。"""
    normalized = (text or "").strip().lower()
    return _ALIAS_MAP.get(normalized, Command.NONE)


def parse(text: str) -> Tuple[Command, str]:
    """把输入拆成 ``(命令, 参数)``。

    - ``"/save"``        -> ``(SAVE, "")``
    - ``"/save 我的会话"`` -> ``(SAVE, "我的会话")``
    - ``"普通问题"``      -> ``(NONE, "普通问题")``
    """
    raw = (text or "").strip()
    if not raw:
        return Command.NONE, ""
    command = _ALIAS_MAP.get(raw.lower())
    if command is not None:
        return command, ""
    head, _, rest = raw.partition(" ")
    command = _ALIAS_MAP.get(head.strip().lower())
    if command is None:
        return Command.NONE, raw
    return command, rest.strip()


def command_rows() -> List[Tuple[str, str]]:
    """命令表格数据：``[(命令, 说明), ...]``，供各界面渲染 help。"""
    return [(spec.name, spec.summary) for spec in COMMAND_TABLE]


def command_names() -> List[str]:
    """全部主命令名（含别名），供输入补全面板匹配。"""
    names: List[str] = []
    for spec in COMMAND_TABLE:
        names.append(spec.name)
        names.extend(f"/{alias}" if not alias.startswith("/") else alias
                     for alias in spec.aliases)
    return names


def suggest(prefix: str, limit: int = 8) -> List[CommandSpec]:
    """按前缀匹配命令（供斜杠命令补全面板使用）。

    ``"/co"`` -> ``[/compact, /config]``；空前缀返回空列表。
    """
    text = (prefix or "").strip().lower()
    if not text.startswith("/") or len(text) < 1:
        return []
    matched = [
        spec for spec in COMMAND_TABLE
        if spec.name.startswith(text)
        or any(
            (alias if alias.startswith("/") else f"/{alias}").startswith(text)
            for alias in spec.aliases
        )
    ]
    return matched[: max(1, int(limit))]


__all__ = [
    "Command",
    "CommandSpec",
    "COMMAND_TABLE",
    "classify",
    "parse",
    "command_rows",
    "command_names",
    "suggest",
]
