# -*- coding: utf-8 -*-
"""Agent Skills 发现与元信息注册。

目录约定::

    <项目根目录>/.agents/skills/<skill-name>/skill.md

其中 ``skill.md`` 必须以 YAML Frontmatter 开头，并声明 ``name`` 与
``description`` 两个必填字段::

    ---
    name: pdf-processing
    description: 从 PDF 中提取文本与表格
    ---

初始化阶段只解析 Frontmatter 区域，**不读取正文**，因此不会把大量
技能说明提前塞进上下文；正文与附加文件由 ``loadSkill`` 工具按需读取。

解析失败、缺少必填字段或名称重复的 skill 会被跳过，并记录在
:attr:`SkillCatalog.warnings` 中，不影响其它 skill 与 Agent 启动。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

#: skills 根目录名（项目根目录下）
SKILLS_DIRNAME = ".agents"
#: skills 根目录下的子目录名
SKILLS_SUBDIR = "skills"
#: 每个 skill 目录中的入口文件名
SKILL_ENTRY_NAME = "skill.md"
#: Frontmatter 最大字符数，避免异常文件导致初始化读取全文
MAX_FRONTMATTER_CHARS = 64 * 1024

PathLike = Union[str, os.PathLike]


@dataclass(frozen=True)
class Skill:
    """单个 skill 的元信息（不含正文）。"""

    name: str
    description: str
    directory: Path
    entry_file: Path

    @property
    def directory_name(self) -> str:
        """skill 子目录名（可作为 ``name`` 的别名使用）。"""
        return self.directory.name

    def relative_path(self, path: Path) -> str:
        """把 skill 目录内的路径转换为 POSIX 风格的相对路径。"""
        try:
            return path.relative_to(self.directory).as_posix()
        except ValueError:
            return str(path)


@dataclass(frozen=True)
class SkillCatalog:
    """初始化时发现的 skill 集合。"""

    project_root: Path
    skills_dir: Path
    skills: Tuple[Skill, ...] = ()
    warnings: Tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.skills)

    @property
    def is_empty(self) -> bool:
        return not self.skills

    def names(self) -> List[str]:
        """按发现顺序返回全部 skill 名称。"""
        return [skill.name for skill in self.skills]

    def find(self, name: str) -> Optional[Skill]:
        """按名称查找 skill。

        优先精确匹配 Frontmatter 中的 ``name``；未命中时再做大小写不敏感
        匹配，并允许使用 skill 目录名作为别名。存在歧义时返回 ``None``。
        """
        query = str(name or "").strip()
        if not query:
            return None

        for skill in self.skills:
            if skill.name == query:
                return skill

        key = query.casefold()
        matches = [
            skill
            for skill in self.skills
            if skill.name.casefold() == key
            or skill.directory_name.casefold() == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def entries_for_prompt(self) -> List[Tuple[str, str]]:
        """供 System Prompt 使用的 ``(name, description)`` 列表。"""
        return [(skill.name, skill.description) for skill in self.skills]


def _strip_inline_comment(value: str) -> str:
    """去掉未加引号 YAML 标量末尾的 ``# 注释``。"""
    marker = value.find(" #")
    if marker >= 0:
        return value[:marker].rstrip()
    return value


def _unescape_double_quoted(value: str) -> str:
    """处理常见的双引号转义（避免引入 PyYAML 依赖）。"""
    replacements = {
        "\\n": "\n",
        "\\r": "\r",
        "\\t": "\t",
        '\\"': '"',
        "\\\\": "\\",
    }
    result: List[str] = []
    index = 0
    while index < len(value):
        pair = value[index:index + 2]
        if pair in replacements:
            result.append(replacements[pair])
            index += 2
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


def _parse_scalar(value: str) -> str:
    """解析单行 YAML 标量（字符串 / 带引号字符串 / 简单注释）。"""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _unescape_double_quoted(text[1:-1])
    return _strip_inline_comment(text)


def _read_block_scalar(
    lines: Sequence[str], start: int, folded: bool
) -> Tuple[str, int]:
    """读取 ``|`` / ``>`` 形式的块标量，返回 (文本, 下一行索引)。"""
    block: List[str] = []
    index = start
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        if raw.strip() and not raw[:1].isspace():
            break
        block.append(raw)
        index += 1

    while block and not block[-1].strip():
        block.pop()

    indents = [
        len(line) - len(line.lstrip()) for line in block if line.strip()
    ]
    indent = min(indents) if indents else 0
    block = [
        line[indent:] if len(line) >= indent else line
        for line in block
    ]

    if folded:
        paragraphs: List[str] = []
        current: List[str] = []
        for line in block:
            if line.strip():
                current.append(line.strip())
            else:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                paragraphs.append("")
        if current:
            paragraphs.append(" ".join(current))
        text = "\n".join(paragraphs)
    else:
        text = "\n".join(block)
    return text.strip("\n"), index


def _read_list_items(
    lines: Sequence[str], start: int
) -> Tuple[List[str], int]:
    """读取 ``- item`` 形式的 YAML 列表，返回 (条目, 下一行索引)。"""
    items: List[str] = []
    index = start
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        if not raw[:1].isspace() or not stripped.startswith("-"):
            break
        items.append(_parse_scalar(stripped[1:].strip()))
        index += 1
    return items, index


def _parse_frontmatter(lines: Sequence[str]) -> Dict[str, str]:
    """解析 Frontmatter 顶层字段（值为字符串，列表用逗号连接）。"""
    values: Dict[str, str] = {}
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        stripped = raw.strip()
        index += 1

        if not stripped or stripped.startswith("#"):
            continue
        # 顶层字段不能有缩进；缩进行属于上一个字段或嵌套结构
        if raw[:1].isspace():
            continue
        if ":" not in raw:
            continue

        key, _, value = raw.partition(":")
        key = key.strip().casefold()
        if not key:
            continue

        value = value.strip()
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            text, index = _read_block_scalar(
                lines, index, folded=value.startswith(">")
            )
            values[key] = text
        elif not value:
            items, next_index = _read_list_items(lines, index)
            if items:
                values[key] = ", ".join(items)
                index = next_index
            else:
                values[key] = ""
        else:
            values[key] = _parse_scalar(value)
    return values


def _read_frontmatter(path: Path) -> Optional[Dict[str, str]]:
    """只读取入口文件开头的 Frontmatter 区域。

    :return: 字段字典；文件没有合法 Frontmatter 时返回 ``None``。
    :raises ValueError: Frontmatter 未闭合或异常庞大。
    """
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return None

        lines: List[str] = []
        consumed = len(first)
        for line in handle:
            consumed += len(line)
            if consumed > MAX_FRONTMATTER_CHARS:
                raise ValueError(
                    f"Frontmatter 超过 {MAX_FRONTMATTER_CHARS} 字符上限"
                )
            if line.strip() in ("---", "..."):
                return _parse_frontmatter(lines)
            lines.append(line)

    raise ValueError("Frontmatter 缺少结束分隔符 '---'")


def find_skill_entry(directory: PathLike) -> Optional[Path]:
    """在 skill 目录中查找入口文件（兼容 ``skill.md`` / ``SKILL.md``）。"""
    base = Path(directory)
    try:
        candidates = sorted(
            base.iterdir(), key=lambda item: item.name.casefold()
        )
    except OSError:
        return None

    for candidate in candidates:
        if (
            candidate.is_file()
            and candidate.name.casefold() == SKILL_ENTRY_NAME.casefold()
        ):
            return candidate
    return None


def read_skill_frontmatter(path: PathLike) -> Optional[Dict[str, str]]:
    """读取任意 skill 入口文件的 Frontmatter（市场等场景复用）。"""
    return _read_frontmatter(Path(path))


def parse_frontmatter(text: str) -> Optional[Dict[str, str]]:
    """解析一段完整文本开头的 YAML Frontmatter。

    :return: 字段字典；文本不以 ``---`` 开头时返回 ``None``。
    :raises ValueError: Frontmatter 缺少结束分隔符。
    """
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return _parse_frontmatter(lines[1:index])
    raise ValueError("Frontmatter 缺少结束分隔符 '---'")


def discover_skills(project_root: Optional[PathLike] = None) -> SkillCatalog:
    """扫描项目根目录下的 ``.agents/skills`` 并注册 skill 元信息。

    :param project_root: 项目根目录；默认使用进程当前工作目录
                         （即程序启动时的工作目录）。
    """
    root = Path(project_root).expanduser().resolve() if project_root else (
        Path(os.getcwd()).resolve()
    )
    skills_dir = root / SKILLS_DIRNAME / SKILLS_SUBDIR

    if not skills_dir.is_dir():
        return SkillCatalog(project_root=root, skills_dir=skills_dir)

    skills: List[Skill] = []
    warnings: List[str] = []
    seen: Dict[str, str] = {}

    try:
        children = sorted(
            skills_dir.iterdir(), key=lambda item: item.name.casefold()
        )
    except OSError as exc:
        return SkillCatalog(
            project_root=root,
            skills_dir=skills_dir,
            warnings=(f"无法读取 skills 目录 {skills_dir}: {exc}",),
        )

    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue

        entry = find_skill_entry(child)
        if entry is None:
            warnings.append(
                f"跳过 {child.name}：缺少 {SKILL_ENTRY_NAME} 文件"
            )
            continue

        try:
            meta = _read_frontmatter(entry)
        except (OSError, ValueError) as exc:
            warnings.append(f"跳过 {child.name}：读取 Frontmatter 失败 -> {exc}")
            continue

        if meta is None:
            warnings.append(
                f"跳过 {child.name}：{entry.name} 开头缺少 YAML Frontmatter"
            )
            continue

        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()

        if not name:
            warnings.append(f"跳过 {child.name}：Frontmatter 缺少 name 字段")
            continue
        if not description:
            warnings.append(
                f"跳过 {child.name}：Frontmatter 缺少 description 字段"
            )
            continue

        key = name.casefold()
        if key in seen:
            warnings.append(
                f"跳过 {child.name}：skill 名称 {name!r} 与 {seen[key]!r} 重复"
            )
            continue
        seen[key] = child.name

        skills.append(
            Skill(
                name=name,
                description=description,
                directory=child.resolve(),
                entry_file=entry.resolve(),
            )
        )

    return SkillCatalog(
        project_root=root,
        skills_dir=skills_dir,
        skills=tuple(skills),
        warnings=tuple(warnings),
    )


__all__ = [
    "MAX_FRONTMATTER_CHARS",
    "SKILL_ENTRY_NAME",
    "SKILLS_DIRNAME",
    "SKILLS_SUBDIR",
    "Skill",
    "SkillCatalog",
    "discover_skills",
    "find_skill_entry",
    "parse_frontmatter",
    "read_skill_frontmatter",
]
