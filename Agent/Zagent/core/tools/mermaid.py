# -*- coding: utf-8 -*-
"""Mermaid 架构图 .mmd 文件操作工具组（4 个 Function Call 工具）。

包含三部分，彼此独立、可整体替换：

1. ``DIAGRAM_TYPES`` / ``_validate_mermaid``   领域规则（Mermaid 语法校验）
2. ``*_mermaid_diagram`` 处理函数             纯业务实现（不打印、不依赖 UI）
3. :func:`build_registry`                     把 4 个工具装配进注册表

支持的图类型::

    flowchart / graph / sequenceDiagram / classDiagram / stateDiagram-v2
    erDiagram / gantt / pie / mindmap / timeline
"""

from __future__ import annotations

import os
from functools import partial
from typing import Optional

from .base import ToolRegistry, ToolSpec

#: 合法的 Mermaid 图类型关键字 -> 中文说明
DIAGRAM_TYPES = {
    "flowchart": "流程图 / 代码架构图",
    "graph": "流程图（旧语法）",
    "sequenceDiagram": "时序图",
    "classDiagram": "类图",
    "stateDiagram-v2": "状态图",
    "erDiagram": "实体关系图（ER 图）",
    "gantt": "甘特图",
    "pie": "饼图",
    "mindmap": "思维导图",
    "timeline": "时间线",
}


# ---------------------------------------------------------------------------
# 领域规则
# ---------------------------------------------------------------------------
def ensure_mmd(filename: str) -> str:
    """规范化文件名：去空白，自动补 .mmd 扩展名。"""
    name = str(filename or "").strip()
    if not name:
        raise ValueError("filename 不能为空")
    if not name.lower().endswith(".mmd"):
        name += ".mmd"
    return name


def diagram_path(filename: str, directory: Optional[str] = None,
                 default_dir: Optional[str] = None) -> str:
    """把文件名解析成绝对路径。

    - 绝对路径：直接使用；
    - 带目录部分的相对路径：基于当前工作目录解析；
    - 纯文件名：落在 ``directory``（或默认 ``default_dir``）下。
    """
    name = ensure_mmd(filename)
    if os.path.isabs(name):
        return name
    if os.path.dirname(name):  # 例如 'out/x.mmd'
        return os.path.abspath(name)
    base = directory.strip() if directory and str(directory).strip() else (
        default_dir or os.path.join(os.getcwd(), "diagrams")
    )
    return os.path.abspath(os.path.join(base, name))


def validate_mermaid(content: str, diagram_type: Optional[str] = None) -> None:
    """校验 Mermaid 源码首行图类型关键字，不合法则抛 ``ValueError``。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("Mermaid 源码不能为空")

    # 跳过注释行（%% 开头）后取第一行
    first_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            first_line = stripped
            break

    head = first_line.split()[0] if first_line.split() else ""
    if head not in DIAGRAM_TYPES:
        raise ValueError(
            f"Mermaid 源码首行必须是图类型关键字之一：{', '.join(DIAGRAM_TYPES)}；"
            f"实际首行为 {first_line!r}"
        )
    if diagram_type and head != diagram_type and not (
        {head, diagram_type} <= {"flowchart", "graph"}
    ):
        raise ValueError(f"声明的 diagram_type={diagram_type} 与源码首行关键字 {head} 不一致")


# ---------------------------------------------------------------------------
# 工具处理函数（纯业务，返回给模型的字符串）
# ---------------------------------------------------------------------------
def create_mermaid_diagram(filename: str, content: str,
                           diagram_type: Optional[str] = None,
                           directory: Optional[str] = None,
                           overwrite: bool = False,
                           *, default_dir: Optional[str] = None) -> str:
    """createMermaidDiagram：生成架构图并保存为 .mmd 纯文本文件。"""
    validate_mermaid(content, diagram_type)

    path = diagram_path(filename, directory, default_dir)
    if os.path.isdir(path):
        raise ValueError(f"同名目录已存在，无法保存文件 -> {path}")
    if os.path.exists(path) and not overwrite:
        raise ValueError(f"文件已存在（如需覆盖请设置 overwrite=true）-> {path}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    # UTF-8 无 BOM 纯文本，保证任何 Mermaid 工具都能直接渲染
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content.rstrip("\n") + "\n")

    lines = content.count("\n") + 1
    return f"架构图已保存: {path}（Mermaid 纯文本，{lines} 行，{os.path.getsize(path)} 字节）"


def update_mermaid_diagram(filename: str, mode: str = "overwrite", content: str = "",
                           old_str: Optional[str] = None,
                           new_str: Optional[str] = None,
                           *, default_dir: Optional[str] = None) -> str:
    """updateMermaidDiagram：更新已有的 .mmd 文件（overwrite/append/replace）。"""
    path = diagram_path(filename, None, default_dir)
    if not os.path.isfile(path):
        raise ValueError(f".mmd 文件不存在 -> {path}")

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    mode = (mode or "overwrite").lower()
    if mode == "overwrite":
        new_text = content or ""
        validate_mermaid(new_text)
        summary = "整体覆盖"
    elif mode == "append":
        new_text = text.rstrip("\n") + "\n" + (content or "")
        summary = "追加内容"
    elif mode == "replace":
        if old_str is None or new_str is None:
            raise ValueError("replace 模式必须同时提供 old_str 和 new_str")
        if old_str == "":
            raise ValueError("old_str 不能为空")
        count = text.count(old_str)
        if count == 0:
            raise ValueError(f"文件中未找到 old_str -> {old_str!r}")
        if count > 1:
            raise ValueError(f"old_str 在文件中出现 {count} 次，为避免误改请提供唯一片段")
        new_text = text.replace(old_str, new_str)
        summary = "替换 1 处内容"
    else:
        raise ValueError(f"未知 mode -> {mode}（支持 overwrite/append/replace）")

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_text.rstrip("\n") + "\n")

    return f"{summary}成功: {path}（{os.path.getsize(path)} 字节）"


def list_mermaid_diagrams(directory: Optional[str] = None,
                          *, default_dir: Optional[str] = None) -> str:
    """listMermaidDiagrams：列出目录下所有 .mmd 文件。"""
    base = directory.strip() if directory and str(directory).strip() else (
        default_dir or os.path.join(os.getcwd(), "diagrams")
    )
    base = os.path.abspath(os.path.expanduser(base))

    if not os.path.isdir(base):
        raise ValueError(f"目录不存在 -> {base}（可先调用 createMermaidDiagram 创建）")

    files = sorted(
        (e for e in os.scandir(base) if e.is_file() and e.name.lower().endswith(".mmd")),
        key=lambda e: e.name.lower(),
    )
    if not files:
        return f"目录 {base} 下没有 .mmd 架构图文件。"

    lines = [f"目录 {base} 共 {len(files)} 张架构图："]
    for entry in files:
        lines.append(f"  - {entry.name} ({entry.stat().st_size} 字节)")
    return "\n".join(lines)


def read_mermaid_diagram(filename: str, *, default_dir: Optional[str] = None) -> str:
    """readMermaidDiagram：读取 .mmd 文件中的 Mermaid 源码。"""
    path = diagram_path(filename, None, default_dir)
    if not os.path.isfile(path):
        raise ValueError(f".mmd 文件不存在 -> {path}")

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return f"{path} 的 Mermaid 源码：\n{text.strip()}"


# ---------------------------------------------------------------------------
# 工具定义（JSON Schema）
# ---------------------------------------------------------------------------
CREATE_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "文件名，例如 'system-arch' 或 'system-arch.mmd'。",
        },
        "content": {
            "type": "string",
            "description": "完整的 Mermaid 源码，例如 'flowchart TD\\n  A[客户端] --> B[网关]'。",
        },
        "diagram_type": {
            "type": "string",
            "description": "可选。图类型，用于校验源码首行关键字。",
            "enum": list(DIAGRAM_TYPES.keys()),
        },
        "directory": {
            "type": "string",
            "description": "可选。保存目录，默认使用配置中的 diagram_dir。",
        },
        "overwrite": {
            "type": "boolean",
            "description": "可选。同名文件已存在时是否覆盖，默认 false。",
        },
    },
    "required": ["filename", "content"],
}

UPDATE_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": ".mmd 文件名或路径，例如 'system-arch.mmd'。",
        },
        "mode": {
            "type": "string",
            "enum": ["overwrite", "append", "replace"],
            "description": "编辑方式，默认 overwrite。",
        },
        "content": {
            "type": "string",
            "description": "overwrite/append 模式下要写入的 Mermaid 源码。",
        },
        "old_str": {
            "type": "string",
            "description": "replace 模式下要被替换的原字符串（需唯一存在）。",
        },
        "new_str": {
            "type": "string",
            "description": "replace 模式下替换成的新字符串。",
        },
    },
    "required": ["filename", "mode"],
}

LIST_PARAMS = {
    "type": "object",
    "properties": {
        "directory": {
            "type": "string",
            "description": "可选。目录路径，默认使用配置中的 diagram_dir。",
        }
    },
    "required": [],
}

READ_PARAMS = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": ".mmd 文件名或路径，例如 'system-arch.mmd'。",
        }
    },
    "required": ["filename"],
}


def build_registry(diagram_dir: str) -> ToolRegistry:
    """装配 Mermaid 工具组。

    :param diagram_dir: 默认输出目录（由配置注入，工具本身不读全局状态）
    """
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="createMermaidDiagram",
        description=(
            "创作一张代码架构图（Mermaid 格式），并保存为 .mmd 纯文本文件。"
            "filename 为文件名（可不带扩展名，自动补 .mmd），"
            "content 为完整的 Mermaid 源码。"
        ),
        parameters=CREATE_PARAMS,
        handler=partial(create_mermaid_diagram, default_dir=diagram_dir),
    ))
    registry.register(ToolSpec(
        name="updateMermaidDiagram",
        description=(
            "更新已有的 .mmd 架构图文件。"
            "mode=overwrite 整体覆盖；mode=append 在末尾追加；"
            "mode=replace 把文件中所有 old_str 替换为 new_str。"
        ),
        parameters=UPDATE_PARAMS,
        handler=partial(update_mermaid_diagram, default_dir=diagram_dir),
    ))
    registry.register(ToolSpec(
        name="listMermaidDiagrams",
        description="列出指定目录下所有 .mmd 架构图文件。",
        parameters=LIST_PARAMS,
        handler=partial(list_mermaid_diagrams, default_dir=diagram_dir),
    ))
    registry.register(ToolSpec(
        name="readMermaidDiagram",
        description="读取指定 .mmd 文件中的 Mermaid 源码内容。",
        parameters=READ_PARAMS,
        handler=partial(read_mermaid_diagram, default_dir=diagram_dir),
    ))
    return registry


__all__ = [
    "DIAGRAM_TYPES",
    "validate_mermaid",
    "build_registry",
    "create_mermaid_diagram",
    "update_mermaid_diagram",
    "list_mermaid_diagrams",
    "read_mermaid_diagram",
]
