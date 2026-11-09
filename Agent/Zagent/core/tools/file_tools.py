# -*- coding: utf-8 -*-
"""通用文件操作工具 ``file0ps``。

用于创建 skill 的 ``references/``、``scripts/`` 等辅助目录与文件，也适用于
项目中普通文本文件的读写。设计要点：

- 所有路径都解析到项目根目录之内，拒绝 ``..`` 或绝对路径越界；
- 写入默认不覆盖已有文件，需要覆盖时必须显式传 ``overwrite=true``；
- 读取只接受文本文件，二进制内容会被拒绝，过长内容会截断并标注；
- 纯业务实现：不打印、不依赖界面，结果文本直接回传给模型。
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import Optional

from .base import ToolRegistry, ToolSpec

#: 单次读取返回给模型的最大字符数
MAX_READ_CHARS = 8000
#: list 单次最多展示的目录条目数
MAX_LIST_ENTRIES = 500

#: 支持的操作
ACTIONS = ("write", "append", "mkdir", "list", "read")


def _project_root(project_root: Optional[str]) -> Path:
    if project_root and str(project_root).strip():
        return Path(project_root).expanduser().resolve()
    return Path(os.getcwd()).resolve()


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _resolve_path(path: str, root: Path) -> Path:
    """把 ``path`` 解析为项目根目录内的绝对路径，越界时抛出 ``ValueError``。"""
    raw = _strip_quotes(path)
    if not raw:
        raise ValueError("path 不能为空")

    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"拒绝访问项目目录之外的路径 -> {resolved}"
        ) from exc
    return resolved


def _truncate(text: str, limit: int = MAX_READ_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return (
        f"{text[:limit]}\n"
        f"[内容已截断：省略了 {omitted} 个字符，原总长度 {len(text)} 字符]"
    )


def file_ops(
    action: str,
    path: Optional[str] = None,
    content: Optional[str] = None,
    overwrite: bool = False,
    *,
    project_root: Optional[str] = None,
    max_read_chars: int = MAX_READ_CHARS,
    max_list_entries: int = MAX_LIST_ENTRIES,
) -> str:
    """file0ps：对项目内的文件 / 目录执行读写与列举操作。

    :param action: ``write`` / ``append`` / ``mkdir`` / ``list`` / ``read``
    :param path: 目标路径，相对项目根目录或使用项目内绝对路径；
                 ``list`` 省略时表示项目根目录
    :param content: ``write`` / ``append`` 要写入的文本
    :param overwrite: ``write`` 时是否覆盖已有文件，默认 ``False``
    :param project_root: 项目根目录，默认为当前工作目录
    :param max_read_chars: 单次读取返回的最大字符数（来自 config.ini）
    :param max_list_entries: list 最多展示的条目数（来自 config.ini）
    """
    root = _project_root(project_root)
    act = str(action or "").strip().lower()
    if act not in ACTIONS:
        raise ValueError(
            f"action 必须是 {' / '.join(ACTIONS)} 之一，实际为 {action!r}"
        )

    if act == "list" and not str(path or "").strip():
        target = root
    else:
        target = _resolve_path(path or "", root)

    if act in ("write", "append"):
        if content is None:
            raise ValueError(f"{act} 操作需要提供 content")
        text = str(content)
        if target.exists() and target.is_dir():
            raise ValueError(f"目标路径是目录，无法写入文件 -> {target}")
        if act == "write" and target.exists() and not overwrite:
            raise ValueError(
                f"文件已存在（如需覆盖请设置 overwrite=true）-> {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if act == "write" else "a"
        with target.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        verb = "已写入" if act == "write" else "已追加"
        return f"{verb}文件: {target}（{target.stat().st_size} 字节，UTF-8）"

    if act == "mkdir":
        if target.exists() and not target.is_dir():
            raise ValueError(f"同名文件已存在，无法创建目录 -> {target}")
        existed = target.is_dir()
        target.mkdir(parents=True, exist_ok=True)
        return f"目录{'已存在' if existed else '已创建'}: {target}"

    if act == "list":
        if not target.exists():
            raise ValueError(f"路径不存在 -> {target}")
        if target.is_file():
            return f"{target} 是文件（{target.stat().st_size} 字节）"
        entries = sorted(
            target.iterdir(),
            key=lambda item: (item.is_file(), item.name.casefold()),
        )
        lines = [f"目录: {target}", f"条目: {len(entries)}"]
        for entry in entries[:max_list_entries]:
            if entry.is_dir():
                lines.append(f"[DIR ] {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = -1
                lines.append(f"[FILE] {entry.name}（{size} 字节）")
        if len(entries) > max_list_entries:
            lines.append(
                f"... 其余 {len(entries) - max_list_entries} 项已省略"
            )
        return "\n".join(lines)

    # act == "read"
    if not target.exists():
        raise ValueError(f"文件不存在 -> {target}")
    if not target.is_file():
        raise ValueError(f"目标不是文件 -> {target}")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取文件失败 -> {target}: {exc}") from exc
    if b"\x00" in data[:8192]:
        raise ValueError(f"目标不是文本文件（包含 NUL 字节）-> {target}")
    return _truncate(
        data.decode("utf-8-sig", errors="replace"), max_read_chars
    )


def build_registry(
    project_root: Optional[str] = None,
    *,
    max_read_chars: int = MAX_READ_CHARS,
    max_list_entries: int = MAX_LIST_ENTRIES,
) -> ToolRegistry:
    """装配通用文件操作工具 ``file0ps``。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="file0ps",
        description=(
            "通用文件 / 目录操作工具：读写文本文件、追加内容、创建目录、列目录。"
            "用于创建 skill 的 references/、scripts/ 等辅助目录与文件，"
            "也可读写项目中的普通文本文件。"
            "path 相对项目根目录解析（也支持项目内的绝对路径），"
            "不允许访问项目根目录之外的位置。"
            "write 默认不覆盖已有文件，需要覆盖时显式设置 overwrite=true；"
            f"read 返回 UTF-8 文本（单次最多 {max_read_chars} 字符，过长会截断）；"
            f"list 最多列出 {max_list_entries} 个条目。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": (
                        "必填。操作类型：write 写文件（可新建）、append 追加、"
                        "mkdir 创建目录、list 列出目录、read 读取文本文件。"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "目标路径，相对项目根目录或项目内绝对路径；"
                        "list 操作省略时表示项目根目录。"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "write / append 要写入的文本内容。",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "write 时是否覆盖已有文件，默认 false。",
                },
            },
            "required": ["action"],
        },
        handler=partial(
            file_ops,
            project_root=project_root,
            max_read_chars=max_read_chars,
            max_list_entries=max_list_entries,
        ),
    ))
    return registry


__all__ = [
    "ACTIONS",
    "MAX_LIST_ENTRIES",
    "MAX_READ_CHARS",
    "build_registry",
    "file_ops",
]
