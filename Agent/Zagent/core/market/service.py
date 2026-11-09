# -*- coding: utf-8 -*-
"""本地 Skill 市场：扫描、搜索、安装与卸载技能包。

市场目录约定::

    <项目根目录>/market/<package>/skill.md

每个包就是一份标准 Agent Skill（``skill.md`` + 任意附加文件）。
安装即把整个包复制到 ``<项目根目录>/.agents/skills/<name>/``，
卸载则从该目录移除；安装 / 卸载后由 :class:`~core.agent.service.AgentService`
刷新 skill 目录和 System Prompt。

可选 Frontmatter 字段（用于市场展示）：

- ``version``    版本号，例如 ``1.0.0``
- ``author``     作者
- ``category``   分类
- ``tags``       标签，支持逗号分隔、``[a, b]`` 或 YAML 列表
- ``homepage``   主页
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

from ..skills import (
    SKILLS_DIRNAME,
    SKILLS_SUBDIR,
    SkillCatalog,
    discover_skills,
    find_skill_entry,
    read_skill_frontmatter,
)

#: 默认市场目录名（项目根目录下）
DEFAULT_MARKET_DIRNAME = "market"
#: 复制技能包时忽略的临时 / 版本控制内容
_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".git", ".svn", ".DS_Store", "Thumbs.db"
)
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketPackage:
    """市场中的一个可安装技能包。"""

    name: str
    description: str
    directory: Path
    entry_file: Path
    version: str = ""
    author: str = ""
    category: str = "未分类"
    tags: Tuple[str, ...] = ()
    homepage: str = ""
    installed: bool = False
    installed_directory: Optional[Path] = None
    #: 是否来自市场源目录；False 表示只是本地已安装技能
    in_market: bool = True

    @property
    def status(self) -> str:
        return "已安装" if self.installed else "可安装"

    @property
    def source_type(self) -> str:
        return "市场包" if self.in_market else "本地已安装"

    @property
    def search_text(self) -> str:
        parts = [
            self.name,
            self.description,
            self.version,
            self.author,
            self.category,
            self.homepage,
            *self.tags,
        ]
        return " ".join(parts).casefold()

    @property
    def source_label(self) -> str:
        return self.directory.name

    def files(self) -> Tuple[str, ...]:
        """技能包内全部文件（相对技能包目录的 POSIX 路径）。"""
        result: List[str] = []
        if not self.directory.is_dir():
            return ()
        for path in sorted(self.directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.directory)
            if any(
                part in ("__pycache__", ".git", ".svn")
                for part in relative.parts
            ):
                continue
            result.append(relative.as_posix())
        return tuple(result)

    @property
    def file_count(self) -> int:
        return len(self.files())

    def matches(self, query: str) -> bool:
        terms = [item for item in (query or "").casefold().split() if item]
        if not terms:
            return True
        haystack = self.search_text
        return all(term in haystack for term in terms)


@dataclass(frozen=True)
class MarketListing:
    """一次市场扫描结果。"""

    market_dir: Path
    skills_dir: Path
    packages: Tuple[MarketPackage, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def installed_count(self) -> int:
        return sum(1 for package in self.packages if package.installed)

    @property
    def market_count(self) -> int:
        return sum(1 for package in self.packages if package.in_market)

    def filtered(self, query: str) -> "MarketListing":
        terms = [item for item in (query or "").casefold().split() if item]
        if not terms:
            return self
        return replace(
            self,
            packages=tuple(
                package
                for package in self.packages
                if all(term in package.search_text for term in terms)
            ),
        )


@dataclass(frozen=True)
class InstallResult:
    """一次安装的结果。"""

    name: str
    target: Path
    version: str
    replaced: bool = False


@dataclass(frozen=True)
class UninstallResult:
    """一次卸载的结果。"""

    name: str
    removed: Path


# ---------------------------------------------------------------------------
# 路径与名称安全
# ---------------------------------------------------------------------------
def _is_within(path: Path, directory: Path) -> bool:
    try:
        base = os.path.normcase(str(directory.resolve()))
        target = os.path.normcase(str(path.resolve()))
        return os.path.commonpath([base, target]) == base
    except (OSError, ValueError):
        return False


def safe_directory_name(name: str) -> str:
    """校验 skill 名称可安全地作为目录名使用。"""
    text = str(name or "").strip()
    if not text:
        raise ValueError("skill 名称不能为空")
    if text in (".", ".."):
        raise ValueError(f"skill 名称非法 -> {name!r}")
    if len(text) > 64:
        raise ValueError(f"skill 名称过长（最多 64 字符）-> {name!r}")
    if any(char in _WINDOWS_INVALID_CHARS or ord(char) < 32 for char in text):
        raise ValueError(f"skill 名称包含非法字符 -> {name!r}")
    if text.endswith((" ", ".")):
        raise ValueError(f"skill 名称不能以空格或点结尾 -> {name!r}")
    if text.split(".")[0].upper() in _WINDOWS_RESERVED:
        raise ValueError(f"skill 名称是系统保留名 -> {name!r}")
    return text


def _parse_tags(raw: str) -> Tuple[str, ...]:
    text = str(raw or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    items = []
    for chunk in text.replace(";", ",").split(","):
        item = chunk.strip().strip("'\"")
        if item:
            items.append(item)
    return tuple(dict.fromkeys(items))


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


# ---------------------------------------------------------------------------
# 市场服务
# ---------------------------------------------------------------------------
class SkillMarket:
    """本地 Skill 市场的业务实现（不依赖任何界面）。"""

    def __init__(
        self,
        project_root: os.PathLike | str,
        market_dir: Optional[os.PathLike | str] = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        base = (
            Path(market_dir).expanduser()
            if market_dir and str(market_dir).strip()
            else self.project_root / DEFAULT_MARKET_DIRNAME
        )
        self.market_dir = base.resolve()
        self.skills_dir = (
            self.project_root / SKILLS_DIRNAME / SKILLS_SUBDIR
        ).resolve()

    # -- 扫描 ---------------------------------------------------------------
    def list_packages(self) -> MarketListing:
        """扫描市场目录，并标记每个包是否已安装。"""
        installed: SkillCatalog = discover_skills(self.project_root)
        warnings: List[str] = []
        packages: List[MarketPackage] = []
        seen: dict[str, str] = {}

        if not self.market_dir.is_dir():
            warnings.append(
                f"市场目录不存在：{self.market_dir}；"
                f"新建该目录并放入技能包即可。"
            )
            children: List[Path] = []
        else:
            try:
                children = sorted(
                    self.market_dir.iterdir(),
                    key=lambda item: item.name.casefold(),
                )
            except OSError as exc:
                warnings.append(f"无法读取市场目录：{exc}")
                children = []

        market_dirs: set[str] = set()
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue

            entry = find_skill_entry(child)
            if entry is None:
                warnings.append(
                    f"跳过 {child.name}：缺少 skill.md 入口文件"
                )
                continue

            try:
                meta = read_skill_frontmatter(entry)
            except (OSError, ValueError) as exc:
                warnings.append(f"跳过 {child.name}：读取 Frontmatter 失败 -> {exc}")
                continue

            if meta is None:
                warnings.append(
                    f"跳过 {child.name}：skill.md 缺少 YAML Frontmatter"
                )
                continue

            name = str(meta.get("name", "")).strip()
            description = str(meta.get("description", "")).strip()
            if not name or not description:
                warnings.append(
                    f"跳过 {child.name}：Frontmatter 缺少 name 或 description"
                )
                continue

            try:
                safe_directory_name(name)
            except ValueError as exc:
                warnings.append(f"跳过 {child.name}：{exc}")
                continue

            key = name.casefold()
            if key in seen:
                warnings.append(
                    f"跳过 {child.name}：名称 {name!r} 与 {seen[key]!r} 重复"
                )
                continue
            seen[key] = child.name
            market_dirs.add(child.name.casefold())

            installed_skill = installed.find(name)
            category = str(meta.get("category", "")).strip() or "未分类"
            packages.append(
                MarketPackage(
                    name=name,
                    description=description,
                    directory=child.resolve(),
                    entry_file=entry.resolve(),
                    version=str(meta.get("version", "")).strip(),
                    author=str(meta.get("author", "")).strip(),
                    category=category,
                    tags=_parse_tags(meta.get("tags", "")),
                    homepage=str(meta.get("homepage", "")).strip(),
                    installed=installed_skill is not None,
                    installed_directory=(
                        installed_skill.directory if installed_skill else None
                    ),
                    in_market=True,
                )
            )

        # 把“已安装但不在市场源目录中”的技能也展示为本地已安装项，
        # 方便在市场里查看详情和卸载；它们不可通过市场重新安装。
        for skill in installed.skills:
            if (
                skill.name.casefold() in seen
                or skill.directory.name.casefold() in market_dirs
            ):
                continue
            try:
                meta = read_skill_frontmatter(skill.entry_file) or {}
            except (OSError, ValueError) as exc:
                warnings.append(f"读取已安装技能 {skill.name!r} 元信息失败 -> {exc}")
                meta = {}
            packages.append(
                MarketPackage(
                    name=skill.name,
                    description=skill.description,
                    directory=skill.directory,
                    entry_file=skill.entry_file,
                    version=str(meta.get("version", "")).strip(),
                    author=str(meta.get("author", "")).strip(),
                    category=str(meta.get("category", "")).strip() or "本地",
                    tags=_parse_tags(meta.get("tags", "")),
                    homepage=str(meta.get("homepage", "")).strip(),
                    installed=True,
                    installed_directory=skill.directory,
                    in_market=False,
                )
            )

        packages.sort(key=lambda package: package.name.casefold())
        return MarketListing(
            market_dir=self.market_dir,
            skills_dir=self.skills_dir,
            packages=tuple(packages),
            warnings=tuple(warnings),
        )

    def search(self, query: str = "") -> MarketListing:
        """按名称 / 描述 / 分类 / 标签 / 作者搜索。"""
        return self.list_packages().filtered(query)

    def get(self, name: str) -> Optional[MarketPackage]:
        """按名称或市场目录名查找技能包。"""
        query = str(name or "").strip()
        if not query:
            return None
        listing = self.list_packages()
        for package in listing.packages:
            if package.name == query:
                return package
        key = query.casefold()
        matches = [
            package
            for package in listing.packages
            if package.name.casefold() == key
            or package.source_label.casefold() == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    # -- 安装 / 卸载 --------------------------------------------------------
    def install(self, name: str, *, force: bool = False) -> InstallResult:
        """安装市场中的技能包到 ``.agents/skills/<name>``。"""
        package = self.get(name)
        if package is None:
            raise ValueError(f"市场中没有找到技能 {name!r}")
        if not package.in_market:
            raise ValueError(
                f"技能 {package.name!r} 是本地已安装技能，不在市场源目录中，"
                f"无法通过市场重新安装"
            )

        if not _is_within(package.directory, self.market_dir):
            raise ValueError(
                f"拒绝安装市场目录之外的技能包 -> {package.directory}"
            )

        target_name = safe_directory_name(package.name)
        target = (self.skills_dir / target_name).resolve()
        if not _is_within(target, self.skills_dir):
            raise ValueError(f"安装目标越界 -> {target}")

        replaced = target.exists() or target.is_symlink()
        if replaced and not force:
            raise ValueError(
                f"技能 {package.name!r} 已安装 -> {target}；"
                f"如需覆盖请使用 force / --force"
            )

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        staging_root = Path(
            tempfile.mkdtemp(prefix=".install-", dir=str(self.skills_dir))
        )
        staging = staging_root / "package"
        try:
            shutil.copytree(
                package.directory,
                staging,
                ignore=_COPY_IGNORE,
                symlinks=True,
            )
            if find_skill_entry(staging) is None:
                raise ValueError("技能包复制后缺少 skill.md 入口文件")

            if replaced:
                _remove_path(target)
            os.replace(str(staging), str(target))
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        return InstallResult(
            name=package.name,
            target=target,
            version=package.version,
            replaced=replaced,
        )

    def uninstall(self, name: str) -> UninstallResult:
        """从 ``.agents/skills`` 卸载技能（支持名称 / 目录名）。"""
        installed = discover_skills(self.project_root)
        skill = installed.find(name)
        target: Optional[Path] = None

        if skill is not None:
            target = skill.directory
        else:
            try:
                candidate = self.skills_dir / safe_directory_name(name)
            except ValueError:
                candidate = None
            if candidate is not None and candidate.is_dir():
                target = candidate

        if target is None:
            raise ValueError(f"技能 {name!r} 尚未安装")

        target = target.resolve()
        skills_root = self.skills_dir.resolve()
        if target == skills_root or not _is_within(target, skills_root):
            raise ValueError(f"拒绝卸载 skills 目录之外的路径 -> {target}")

        _remove_path(target)
        return UninstallResult(name=name, removed=target)

    # -- 展示辅助 -----------------------------------------------------------
    @staticmethod
    def summary_lines(listing: MarketListing) -> List[str]:
        """把市场列表整理成适合 CLI 展示的行。"""
        if not listing.packages:
            return ["（市场中没有可安装的技能）"]
        lines = []
        for package in listing.packages:
            state = "已安装" if package.installed else "可安装"
            version = package.version or "-"
            source = "" if package.in_market else "  本地"
            lines.append(
                f"[{state}] {package.name}  v{version}  "
                f"({package.category}){source}  {package.description}"
            )
        return lines


__all__ = [
    "DEFAULT_MARKET_DIRNAME",
    "InstallResult",
    "MarketListing",
    "MarketPackage",
    "SkillMarket",
    "UninstallResult",
    "safe_directory_name",
]
