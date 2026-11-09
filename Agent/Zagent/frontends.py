# -*- coding: utf-8 -*-
"""应用外壳（Application Shell）：前端自动发现与实例化。

架构约定（端口-适配器）::

    core/        业务核心，定义端口 core.ports.Frontend
    cli/  tui/  web/   前端适配器，各自导出 ``Frontend`` 类
    desktop/  mobile/  ……未来前端，同样只需导出 ``Frontend``

发现规则：项目根目录下每个含 ``__init__.py`` 的目录都被视为候选前端包
（``core`` 除外），包必须导出 ``Frontend``（:class:`core.ports.Frontend`
的实现类）。因此**新增一个前端 = 新建一个目录**，无需改动 core、
本模块或任何既有前端。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Dict, Optional, Type

from core.ports import Frontend

#: 项目根目录（本文件所在目录）
PROJECT_ROOT = Path(__file__).resolve().parent
#: 非前端包（业务层）
EXCLUDED_PACKAGES = frozenset({"core"})
#: 默认优先使用的前端
_PREFERRED = ("tui", "cli")

_entrypoints: Dict[str, Type[Frontend]] = {}
#: 包名 -> 无法使用的原因（缺少依赖、未导出 Frontend、导入异常等）
_unavailable: Dict[str, str] = {}
_scanned = False


def _scan() -> None:
    global _scanned
    if _scanned:
        return
    _scanned = True

    for pkg_dir in sorted(PROJECT_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not pkg_dir.is_dir():
            continue
        if pkg_dir.name.startswith((".", "_")) or pkg_dir.name in EXCLUDED_PACKAGES:
            continue
        if not (pkg_dir / "__init__.py").is_file():
            continue

        try:
            module = importlib.import_module(pkg_dir.name)
        except Exception as exc:  # 缺少可选依赖（如 textual）或包内代码错误
            _unavailable[pkg_dir.name] = f"{type(exc).__name__}: {exc}"
            continue

        cls = getattr(module, "Frontend", None)
        if not (isinstance(cls, type) and issubclass(cls, Frontend)):
            _unavailable[pkg_dir.name] = "未导出 Frontend（core.ports.Frontend 实现类）"
            continue
        name = getattr(cls, "name", "") or pkg_dir.name
        _entrypoints[name] = cls


def available() -> Dict[str, Type[Frontend]]:
    """可用前端：标识 -> 实现类。"""
    _scan()
    return dict(_entrypoints)


def available_names() -> list:
    return sorted(available())


def unavailable() -> Dict[str, str]:
    """不可用的候选前端包及原因。"""
    _scan()
    return dict(_unavailable)


def default_name() -> Optional[str]:
    """自动选择的默认前端（tui > cli > 任意可用前端）。"""
    names = available_names()
    for preferred in _PREFERRED:
        if preferred in names:
            return preferred
    return names[0] if names else None


def create(name: str) -> Frontend:
    """按标识实例化前端。

    :raises KeyError: 该标识不存在或对应包不可用（``str(exc)`` 含原因）。
    """
    entrypoints = available()
    if name in entrypoints:
        return entrypoints[name]()
    if name in _unavailable:
        raise KeyError(f"前端 {name!r} 不可用: {_unavailable[name]}")
    raise KeyError(
        f"未知前端 {name!r}，可用: {', '.join(entrypoints) or '（无）'}"
    )


__all__ = [
    "PROJECT_ROOT",
    "available",
    "available_names",
    "unavailable",
    "default_name",
    "create",
]
