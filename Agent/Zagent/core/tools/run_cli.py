# -*- coding: utf-8 -*-
"""本地命令行执行工具（RunCli）。

调用本机已安装的任意命令行程序（CLI）：程序名 ``command`` 与参数列表
``args`` 拼装为列表后通过 :func:`subprocess.run` 以 ``shell=False`` 执行，
避免拼接 shell 命令字符串带来的注入与转义问题。

执行结果（stdout / stderr / 退出码）会整理为文本返回给模型，使其能够
判断执行结果并决定下一步操作。输出过长时按上限截断并明确标注截断
情况，避免大量终端输出占用上下文。纯业务实现：不打印、不依赖界面。
"""

from __future__ import annotations

import subprocess
from functools import partial
from typing import List, Optional, Sequence

from .base import ToolRegistry, ToolSpec

#: 默认执行超时（秒），可通过 timeout 参数按需调整
DEFAULT_TIMEOUT_SECONDS = 60.0
#: 单次执行允许的最大超时（秒），防止无限阻塞
MAX_TIMEOUT_SECONDS = 600.0
#: stdout / stderr 各自的截断字符上限
MAX_OUTPUT_CHARS = 8000


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """按字符数截断文本；发生截断时在末尾追加明确的标注。

    :param text: 原始文本
    :param limit: 保留的最大字符数
    :return: 截断后的文本（未超限时原样返回）
    """
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return (
        f"{text[:limit]}\n"
        f"[输出已截断：省略了 {omitted} 个字符，原总长度 {len(text)} 字符]"
    )


def run_cli(
    command: str,
    args: Optional[Sequence[str]] = None,
    timeout: Optional[float] = None,
    *,
    default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_timeout: float = MAX_TIMEOUT_SECONDS,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    """RunCli：在本机执行指定命令行程序并返回 stdout/stderr/退出码。

    :param command: 程序名称或可执行文件路径，如 ``"python"``、``"git"``
    :param args: 参数列表，如 ``["--version"]``；为空时执行无参命令
    :param timeout: 可选。执行超时秒数，默认取 ``default_timeout``
    :param default_timeout: 默认超时（config.ini ``[tools] cli_timeout``）
    :param max_timeout: 超时上限（config.ini ``[tools] cli_max_timeout``）
    :param max_chars: 输出截断长度（config.ini ``[tools] cli_max_chars``）
    :raises ValueError: command 为空，或 timeout 非法
    """
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("command 不能为空，请提供程序名称或可执行文件路径")

    argv: List[str] = [cmd]
    for item in args or ():
        argv.append(str(item))

    if timeout is None:
        effective_timeout = float(default_timeout)
    else:
        try:
            effective_timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"timeout 必须是数字（秒）-> {timeout!r}") from exc
        if effective_timeout <= 0:
            raise ValueError(f"timeout 必须为正数（秒）-> {timeout!r}")
        effective_timeout = min(effective_timeout, float(max_timeout))

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=effective_timeout,
        )
    except FileNotFoundError:
        return (
            f"错误：找不到命令 {cmd!r}，请确认程序已安装且在 PATH 中，"
            f"或提供可执行文件完整路径。"
        )
    except PermissionError:
        return f"错误：没有权限执行 {cmd!r}，请检查文件权限。"
    except subprocess.TimeoutExpired:
        return (
            f"执行超时：命令 {' '.join(argv)!r} 在 {effective_timeout:g} 秒内未结束，已强制终止。"
            f"可尝试简化命令，或通过 timeout 参数增大超时（上限 {max_timeout:g} 秒）。"
        )
    except OSError as exc:
        return f"错误：执行 {' '.join(argv)!r} 失败 -> {exc}"

    stdout = _truncate(completed.stdout or "", max_chars)
    stderr = _truncate(completed.stderr or "", max_chars)
    exit_code = completed.returncode

    lines = [f"命令: {' '.join(argv)}", f"退出码: {exit_code}"]
    lines.append("stdout:\n" + (stdout if stdout else "(空)"))
    lines.append("stderr:\n" + (stderr if stderr else "(空)"))
    if exit_code != 0:
        lines.append("提示：退出码非 0，命令执行失败，请结合 stderr 判断原因。")
    return "\n".join(lines)


def build_registry(
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_timeout: float = MAX_TIMEOUT_SECONDS,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> ToolRegistry:
    """注册本地命令行执行工具组（参数来自 config.ini 的 ``[tools]`` 段）。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="RunCli",
        description=(
            "在用户本机执行已安装的命令行程序（CLI）。"
            "传入程序名称 command 与参数列表 args，以 shell=False 方式安全执行，"
            "返回 stdout、stderr 与退出码，供你判断执行结果并决定下一步操作。"
            "适用场景：查看 git 状态、运行 python/脚本、查询系统信息、调用任意已安装 CLI 等。"
            "注意：命令在用户机器上真实执行，请勿执行有破坏性的危险命令；"
            f"输出过长时会被截断（上限 {max_chars} 字符）并标注。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "程序名称或可执行文件路径，例如 'python'、'git'、'node'。"
                        "不含参数。"
                    ),
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选。参数列表（字符串数组），例如 ['status', '--short']。"
                        "不要传入 shell 元字符；每个参数独立一项。"
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        f"可选。执行超时秒数，默认 {timeout:g}，"
                        f"上限 {max_timeout:g}。超时后命令被强制终止并返回超时信息。"
                    ),
                },
            },
            "required": ["command"],
        },
        handler=partial(
            run_cli,
            default_timeout=timeout,
            max_timeout=max_timeout,
            max_chars=max_chars,
        ),
    ))
    return registry


__all__ = [
    "run_cli",
    "build_registry",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "MAX_OUTPUT_CHARS",
]
