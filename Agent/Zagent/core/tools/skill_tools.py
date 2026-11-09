# -*- coding: utf-8 -*-
"""Skill 相关工具：``loadSkill`` 按需加载、``createSKill`` 创建新 skill。

``loadSkill``：

- 只传 ``name``：返回该 skill 的 ``skill.md`` 全文；
- 同时传 ``filename``：只读取对应文件，不会重复附带 ``skill.md``；
- 文件解析顺序：绝对路径 / skill 目录相对路径 / 项目相对路径；
- 精确路径不存在时，在该 skill 目录中递归按文件名或路径后缀查找，
  唯一匹配则读取，多个同名文件则返回候选路径交给模型选择；
- 所有读取都被限制在指定 skill 目录内部，避免路径穿越读取其它文件。

``createSKill``：

- 在 ``<项目根目录>/.agents/skills/<name>`` 下生成 ``SKILL.md``；
- ``name`` 统一规范化为小写字母、数字与连字符；
- 自动校验 / 补齐 YAML Frontmatter 的 ``name`` 与 ``description``；
- 目标 skill 已存在时返回明确提示，绝不覆盖原有内容；
- ``references/``、``scripts/`` 等辅助文件由模型继续调用 ``file0ps`` 创建。
"""

from __future__ import annotations

import os
import re
import shutil
from functools import partial
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from ..skills import (
    Skill,
    SkillCatalog,
    find_skill_entry,
    parse_frontmatter,
)
from .base import ToolRegistry, ToolSpec

#: 既支持固定目录，也支持“每次调用时重新获取”的目录提供器。
#: 后者让市场安装 / 卸载技能后无需重建工具即可立即生效。
CatalogSource = Union[SkillCatalog, Callable[[], SkillCatalog]]


def _current_catalog(source: CatalogSource) -> SkillCatalog:
    catalog = source() if callable(source) else source
    if not isinstance(catalog, SkillCatalog):
        raise ValueError("skill 目录不可用，请检查 Agent 初始化状态")
    return catalog


def _is_within(path: Path, directory: Path) -> bool:
    """判断 ``path`` 是否位于 ``directory`` 内部（含目录本身）。"""
    try:
        base = os.path.normcase(str(directory.resolve()))
        target = os.path.normcase(str(path.resolve()))
        return os.path.commonpath([base, target]) == base
    except (OSError, ValueError):
        return False


def _strip_matching_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _normalize_parts(value: str) -> List[str]:
    """把路径拆成规范化的片段，兼容 ``/`` 与 ``\\`` 分隔符。"""
    normalized = value.replace("\\", "/")
    return [part for part in normalized.split("/") if part not in ("", ".")]


def _relative_label(path: Path, skill: Skill) -> str:
    return skill.relative_path(path.resolve())


def _read_text_file(path: Path, skill: Skill) -> str:
    """读取 skill 目录内的文本文件；拒绝目录与明显的二进制文件。"""
    resolved = path.resolve()
    if not _is_within(resolved, skill.directory):
        raise ValueError(
            f"拒绝读取 skill 目录之外的文件 -> {resolved}"
        )
    if not resolved.exists():
        raise ValueError(f"文件不存在 -> {_relative_label(resolved, skill)}")
    if not resolved.is_file():
        raise ValueError(
            f"路径不是文件 -> {_relative_label(resolved, skill)}"
        )

    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise ValueError(f"读取文件失败 -> {resolved}: {exc}") from exc

    if b"\x00" in data[:8192]:
        raise ValueError(
            f"目标不是文本文件（包含 NUL 字节）-> {_relative_label(resolved, skill)}"
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _match_exact(
    skill: Skill,
    raw: str,
    parts: Sequence[str],
    project_root: Optional[Path],
) -> Optional[Path]:
    """按“绝对路径 -> skill 相对 -> 项目相对”的顺序查找精确路径。

    若某个已存在的精确路径落在 skill 目录之外，直接报错，避免模型
    用 ``filename`` 读取项目中的其它文件。
    """
    query = Path(raw)
    relative = os.sep.join(parts)

    candidates: List[Path] = []
    if query.is_absolute():
        candidates.append(query)
    else:
        candidates.append(skill.directory / relative)
        # 纯文件名只在该 skill 目录内解析；带目录部分时才尝试项目相对路径
        if len(parts) > 1:
            if project_root is not None:
                candidates.append(project_root / relative)
            cwd = Path(os.getcwd())
            if project_root is None or cwd.resolve() != project_root.resolve():
                candidates.append(cwd / relative)

    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if not resolved.exists():
            continue
        if not _is_within(resolved, skill.directory):
            raise ValueError(
                f"拒绝读取 skill 目录之外的文件 -> {resolved}"
            )
        return resolved
    return None


def _strip_skill_prefix(
    parts: Sequence[str], skill: Skill
) -> List[str]:
    """允许递归查询中包含 skill 目录名或 ``.agents/skills`` 前缀。"""
    normalized = list(parts)
    prefixes = [
        (".agents", "skills", skill.directory_name),
        (".agents", "skills", skill.name),
        (skill.directory_name,),
        (skill.name,),
    ]
    for prefix in prefixes:
        if len(normalized) <= len(prefix):
            continue
        if [
            item.casefold() for item in normalized[:len(prefix)]
        ] == [item.casefold() for item in prefix]:
            return normalized[len(prefix):]
    return normalized


def _recursive_matches(skill: Skill, parts: Sequence[str]) -> List[Path]:
    """在 skill 目录中递归查找文件名 / 路径后缀匹配的文件。"""
    query = _strip_skill_prefix(parts, skill)
    if not query:
        return []
    target = [item.casefold() for item in query]
    matches: List[Path] = []

    for dirpath, dirnames, filenames in os.walk(skill.directory, followlinks=False):
        # 不跟随目录符号链接，避免跳出 skill 目录
        dirnames[:] = [
            name
            for name in dirnames
            if not (Path(dirpath) / name).is_symlink()
        ]
        for filename in filenames:
            candidate = Path(dirpath) / filename
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not _is_within(resolved, skill.directory) or not resolved.is_file():
                continue
            relative_parts = [
                item.casefold() for item in candidate.relative_to(skill.directory).parts
            ]
            if len(target) == 1:
                matched = relative_parts[-1] == target[0]
            else:
                matched = relative_parts[-len(target):] == target
            if matched:
                matches.append(resolved)

    return sorted(
        matches, key=lambda item: _relative_label(item, skill).casefold()
    )


def _multiple_candidates_message(skill: Skill, matches: Sequence[Path]) -> str:
    lines = [
        f"在 skill '{skill.name}' 的目录中找到多个匹配文件，"
        "请使用更精确的 filename（相对 skill 目录的路径）再次调用 loadSkill："
    ]
    for path in matches:
        lines.append(f"- {_relative_label(path, skill)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# createSKill：创建符合 Agent Skills 规范的新 skill
# ---------------------------------------------------------------------------
#: 写入 skill 目录的入口文件名（与 Agent Skills 规范一致）
SKILL_FILE_NAME = "SKILL.md"
#: Agent Skills 规范：name 最长 64 字符
MAX_SKILL_NAME_LENGTH = 64
#: Agent Skills 规范：description 最长 1024 字符
MAX_DESCRIPTION_LENGTH = 1024
#: 自动生成 description 时从正文首行截取的最大长度
DERIVED_DESCRIPTION_LENGTH = 200

#: Frontmatter 顶层字段行，如 ``name: pdf-processing``
_KEY_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(.*)$")
#: YAML 纯量不能直接以这些字符开头
_PLAIN_UNSAFE_START = set("-?:,[]{}#&*!|>'\"%@`")


def normalize_skill_name(name: str) -> str:
    """把 skill 名称规范化为小写字母、数字与连字符组成的格式。

    例如 ``"PDF Processing"`` -> ``"pdf-processing"``；
    无法规范化（如纯中文）时抛出 ``ValueError``。
    """
    raw = str(name or "").strip()
    if not raw:
        raise ValueError(
            "name 不能为空，请提供英文 skill 名称"
            "（小写字母、数字与连字符，例如 pdf-processing）"
        )

    candidate = raw.casefold()
    candidate = re.sub(r"[^a-z0-9]+", "-", candidate)
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-")

    if not candidate:
        raise ValueError(
            f"skill 名称 {raw!r} 无法规范化为英文格式；"
            "请使用小写字母、数字与连字符（例如 pdf-processing）"
        )
    if len(candidate) > MAX_SKILL_NAME_LENGTH:
        raise ValueError(
            f"skill 名称过长（{len(candidate)} 字符），"
            f"Agent Skills 规范上限为 {MAX_SKILL_NAME_LENGTH} 字符"
        )
    return candidate


def _yaml_scalar(value: str) -> str:
    """把单行文本转成安全的 YAML 标量（必要时加双引号）。"""
    text = " ".join(str(value or "").split())
    if not text:
        return '""'
    plain = (
        text[0] not in _PLAIN_UNSAFE_START
        and ": " not in text
        and " #" not in text
        and not text.endswith(":")
    )
    if plain:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _derive_description(body: Sequence[str]) -> str:
    """从正文首个非空行推导 description（去掉 Markdown 标记）。"""
    for line in body:
        text = line.strip()
        if not text:
            continue
        text = re.sub(r"^#{1,6}\s*", "", text)
        text = re.sub(r"^(?:[-*+>]|\d+[.)])\s+", "", text)
        text = text.strip()
        if text:
            return text[:DERIVED_DESCRIPTION_LENGTH]
    return ""


def _ensure_frontmatter(
    fm_lines: Sequence[str],
    name: str,
    body: Sequence[str],
) -> List[str]:
    """确保 Frontmatter 含 ``name`` 与 ``description``，并统一 name。

    已存在的其它字段与 description 原文保持不变；缺少 description
    时用正文首行自动生成，避免产出无法被 skill 目录识别的文件。
    """
    lines = list(fm_lines)

    def key_index(key: str) -> int:
        for index, line in enumerate(lines):
            if line[:1].isspace():
                continue
            match = _KEY_LINE_RE.match(line)
            if match and match.group(1).casefold() == key:
                return index
        return -1

    index = key_index("name")
    if index >= 0:
        lines[index] = f"name: {name}"
    else:
        lines.insert(0, f"name: {name}")

    index = key_index("description")
    if index >= 0:
        match = _KEY_LINE_RE.match(lines[index])
        value = match.group(2).strip() if match else ""
        if not value:
            has_block = (
                index + 1 < len(lines)
                and lines[index + 1][:1].isspace()
                and bool(lines[index + 1].strip())
            )
            if not has_block:
                derived = _derive_description(body)
                if not derived:
                    raise ValueError(
                        "skillContext 缺少 description，且正文为空，"
                        "无法自动生成描述；请在 Frontmatter 中补充 description"
                    )
                lines[index] = f"description: {_yaml_scalar(derived)}"
    else:
        derived = _derive_description(body)
        if not derived:
            raise ValueError(
                "skillContext 缺少 description，且正文为空，"
                "无法自动生成描述；请在 Frontmatter 中补充 description"
            )
        lines.insert(key_index("name") + 1, f"description: {_yaml_scalar(derived)}")
    return lines


def build_skill_document(name: str, skill_context: str) -> Tuple[str, str]:
    """规范化 SKILL.md 全文，返回 ``(完整文本, description)``。

    - 已有 Frontmatter 时保留其它字段与正文，仅纠正 / 补齐 name 与 description；
    - 没有 Frontmatter 时自动在最前面生成规范的 YAML Frontmatter；
    - 最终文本会再解析一次，确保能被 Agent Skills 目录正确识别。
    """
    text = str(skill_context or "").replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.strip("\n")
    if not text.strip():
        raise ValueError("skillContext 不能为空，请提供 SKILL.md 的完整内容")

    lines = text.split("\n")
    if lines[0].strip() == "---":
        end = -1
        for index in range(1, len(lines)):
            if lines[index].strip() in ("---", "..."):
                end = index
                break
        if end < 0:
            raise ValueError(
                "skillContext 的 YAML Frontmatter 缺少结束分隔符 '---'"
            )
        fm_lines: List[str] = lines[1:end]
        body: List[str] = lines[end + 1:]
    else:
        fm_lines = []
        body = lines

    if not any(line.strip() for line in body):
        raise ValueError(
            "skillContext 正文不能为空：Frontmatter 之后需要包含 skill 的说明内容"
        )

    fm_lines = _ensure_frontmatter(fm_lines, name, body)
    document = (
        "---\n"
        + "\n".join(fm_lines)
        + "\n---\n\n"
        + "\n".join(body).strip("\n")
        + "\n"
    )

    meta = parse_frontmatter(document)
    if meta is None:
        raise ValueError("生成的 SKILL.md 缺少合法的 YAML Frontmatter")
    final_name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if final_name != name:
        raise ValueError(
            f"生成的 SKILL.md 中 name={final_name!r} 与规范化名称 {name!r} 不一致"
        )
    if not description:
        raise ValueError("生成的 SKILL.md 缺少 description")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(
            f"skill description 过长（{len(description)} 字符），"
            f"Agent Skills 规范上限为 {MAX_DESCRIPTION_LENGTH} 字符"
        )
    return document, description


def create_skill(
    name: str,
    skillContext: str,  # 与 Function Calling schema 的参数名保持一致
    *,
    catalog: CatalogSource,
    refresh: Optional[Callable[[], object]] = None,
) -> str:
    """createSKill：创建 ``.agents/skills/<name>/SKILL.md``。

    :param name: skill 名称，会被规范化为小写字母 / 数字 / 连字符
    :param skillContext: 写入 ``SKILL.md`` 的完整内容（可含 Frontmatter）
    :param catalog: 当前 skill 目录（固定对象或每次调用重新获取）
    :param refresh: 创建成功后刷新 skill 目录 / System Prompt 的回调
    :raises ValueError: 名称为空或非法、已有 SKILL.md、内容不符合规范
    """
    current = _current_catalog(catalog)
    raw_name = str(name or "").strip()
    normalized = normalize_skill_name(raw_name)
    target_dir = current.skills_dir / normalized

    # 先检查磁盘目录，再检查已注册名称，双重避免覆盖或重名。
    # 目录已存在但其中没有 SKILL.md 时（例如用户提前放入了可执行文件、
    # 参考资料等资源），允许补写 SKILL.md，且不改动目录中的其它文件。
    has_entry = target_dir.is_dir() and find_skill_entry(target_dir) is not None
    if target_dir.exists() and (not target_dir.is_dir() or has_entry):
        kind = "已有 SKILL.md 的 skill" if has_entry else "同名文件"
        raise ValueError(
            f"skill '{normalized}' 已存在（{kind}：{target_dir}），"
            "未做任何修改。如需重建，请先确认并删除原有目录，或换一个名称。"
        )
    reuse_dir = target_dir.is_dir()
    existing = current.find(normalized)
    if existing is not None:
        raise ValueError(
            f"skill 名称 '{normalized}' 已被现有 skill 使用"
            f"（{existing.directory}），未做任何修改，请换一个名称。"
        )

    document, description = build_skill_document(normalized, skillContext)

    skill_file = target_dir / SKILL_FILE_NAME
    created_dir = False
    created_file = False
    try:
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=False)
            created_dir = True
        # 使用独占创建模式，绝不覆盖已有的 SKILL.md
        with skill_file.open("x", encoding="utf-8", newline="\n") as handle:
            created_file = True
            handle.write(document)
    except OSError as exc:
        if created_file:
            try:
                skill_file.unlink()
            except OSError:
                pass
        if created_dir:
            shutil.rmtree(target_dir, ignore_errors=True)
        raise ValueError(f"创建 skill 失败 -> {target_dir}: {exc}") from exc

    lines: List[str] = []
    if raw_name and raw_name != normalized:
        lines.append(f"名称已规范化：{raw_name!r} -> {normalized!r}")
    if reuse_dir:
        lines.append(
            f"复用已存在目录（其中没有 {SKILL_FILE_NAME}，未改动其它文件）：{target_dir}"
        )
    lines.extend([
        f"已创建 skill '{normalized}'：{skill_file}",
        f"- name: {normalized}",
        f"- description: {description}",
        f"- 入口文件: {SKILL_FILE_NAME}（UTF-8，YAML Frontmatter 已校验）",
    ])

    refreshed = True
    if refresh is not None:
        try:
            refresh()
        except Exception:  # noqa: BLE001 - 刷新失败不影响文件已创建的事实
            refreshed = False
    if not refreshed:
        lines.append("提示：skill 清单刷新失败，重启 Agent 后即可加载该 skill。")

    lines.append(
        f"如需 references/、scripts/ 等辅助文件，请继续调用 file0ps 工具"
        f"在 {target_dir} 下创建；不要覆盖已有 skill。"
    )
    return "\n".join(lines)


def _load_skill_file(
    skill: Skill,
    filename: str,
    project_root: Optional[Path],
) -> str:
    """加载 skill 内的附加文件（支持精确路径与唯一文件名递归匹配）。"""
    raw = _strip_matching_quotes(filename)
    if not raw:
        return _read_text_file(skill.entry_file, skill)

    parts = _normalize_parts(raw)
    if not parts:
        raise ValueError("filename 不能为空")

    # 统一拒绝 '..'，避免任何形式的路径穿越尝试
    if any(part == ".." for part in raw.replace("\\", "/").split("/")):
        raise ValueError(f"filename 不允许包含 '..' -> {filename!r}")

    exact = _match_exact(skill, raw, parts, project_root)
    if exact is not None:
        return _read_text_file(exact, skill)

    matches = _recursive_matches(skill, parts)
    if len(matches) == 1:
        return _read_text_file(matches[0], skill)
    if len(matches) > 1:
        return _multiple_candidates_message(skill, matches)

    raise ValueError(
        f"在 skill '{skill.name}' 的目录中未找到文件 {filename!r}；"
        f"可先只传 name 加载 skill.md，再按其中引用的相对路径读取。"
    )


def load_skill(
    name: str,
    filename: Optional[str] = None,
    *,
    catalog: CatalogSource,
) -> str:
    """loadSkill：按 skill 名称加载完整说明或其中的附加文件。"""
    current = _current_catalog(catalog)
    skill = current.find(name)
    if skill is None:
        available = ", ".join(current.names()) or "（无）"
        raise ValueError(
            f"未找到 skill {str(name or '').strip()!r}；可用 skill: {available}"
        )

    target = str(filename or "").strip()
    if not target:
        return _read_text_file(skill.entry_file, skill)
    return _load_skill_file(skill, target, current.project_root)


def register_skill_tool(registry: ToolRegistry, catalog: CatalogSource) -> None:
    """把 ``loadSkill`` 注册到已有工具注册表（已存在时跳过）。"""
    if registry.get("loadSkill") is not None:
        return

    registry.register(ToolSpec(
        name="loadSkill",
        description=(
            "按需加载一个 Agent Skill 的完整说明。"
            "只传 name 时返回该 skill 的 skill.md 全文；"
            "同时传 filename 时只读取该附加文件（不重复返回 skill.md）。"
            "filename 支持 skill 目录相对路径、项目相对路径与绝对路径；"
            "只提供文件名时会在该 skill 目录内递归查找，唯一匹配则读取，"
            "多个同名文件会返回候选路径供你选择下一次读取的 filename。"
            "仅在确实需要该 skill 的具体操作步骤或附加资料时调用，"
            "不要提前加载所有 skill。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "必填。要加载的 skill 名称（skill.md Frontmatter 中的 name），"
                        "也可使用 skill 目录名。"
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "可选。要加载的附加文件路径或文件名，可相对 skill 目录、"
                        "相对项目根目录或使用绝对路径；只传文件名时会递归查找。"
                        "省略时返回 skill.md 全文。"
                    ),
                },
            },
            "required": ["name"],
        },
        handler=partial(load_skill, catalog=catalog),
    ))


def register_create_skill_tool(
    registry: ToolRegistry,
    catalog: CatalogSource,
    refresh: Optional[Callable[[], object]] = None,
) -> None:
    """把 ``createSKill`` 注册到已有工具注册表（已存在时跳过）。"""
    if registry.get("createSKill") is not None:
        return

    registry.register(ToolSpec(
        name="createSKill",
        description=(
            "按用户要求创建一个新的 Agent Skill：在项目的 "
            ".agents/skills/<name>/ 下生成 SKILL.md。"
            "name 会自动规范化为小写字母、数字与连字符（如 pdf-processing）；"
            "skillContext 必须是完整的 SKILL.md 内容，包含正文，"
            "推荐包含 YAML Frontmatter 的 name 与 description（缺失时会自动补齐）。"
            "如果同名 skill（已有 SKILL.md）已存在，工具只返回提示，不会覆盖原有内容；"
            "若目录已存在但还没有 SKILL.md（例如已放入 exe、参考资料），"
            "会在该目录中补写 SKILL.md 且不改动其它文件。"
            "references/、scripts/ 等辅助目录或文件请继续调用 file0ps 工具创建。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "必填。skill 名称，例如 pdf-processing；"
                        "会统一转换为小写字母、数字与连字符。"
                    ),
                },
                "skillContext": {
                    "type": "string",
                    "description": (
                        "必填。写入 SKILL.md 的完整内容，包括 YAML Frontmatter"
                        "（name、description）与 skill 正文说明。"
                    ),
                },
            },
            "required": ["name", "skillContext"],
        },
        handler=partial(create_skill, catalog=catalog, refresh=refresh),
    ))


def build_registry(
    catalog: CatalogSource,
    refresh: Optional[Callable[[], object]] = None,
) -> ToolRegistry:
    """创建包含 ``loadSkill`` 与 ``createSKill`` 的工具组。"""
    registry = ToolRegistry()
    register_skill_tool(registry, catalog)
    register_create_skill_tool(registry, catalog, refresh)
    return registry


__all__ = [
    "CatalogSource",
    "SKILL_FILE_NAME",
    "build_skill_document",
    "create_skill",
    "load_skill",
    "normalize_skill_name",
    "register_create_skill_tool",
    "register_skill_tool",
    "build_registry",
]
