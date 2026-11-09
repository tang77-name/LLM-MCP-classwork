# -*- coding: utf-8 -*-
"""工具层公共 API。

- 单个工具组：:func:`build_mermaid_registry` / :func:`build_datetime_registry` /
  :func:`build_web_search_registry` / :func:`build_fetch_web_registry` /
  :func:`build_run_cli_registry` / :func:`build_file_registry` /
  :func:`build_skill_registry`
- 应用默认工具集：:func:`build_default_registry`（组合全部工具组）

新增工具组：实现 ``build_registry() -> ToolRegistry`` 并在
:func:`build_default_registry` 中组合注册即可，模型 schema 与执行分发
会同步生效。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from ..config import DEFAULT_SEARCH, DEFAULT_TOOLS
from .base import ToolHandler, ToolRegistry, ToolSpec
from .context_tools import register_recall_tool as register_recall_tool
from .mermaid import build_registry as build_mermaid_registry
from .datetime_tools import build_registry as build_datetime_registry
from .web_search import build_registry as build_web_search_registry
from .fetch_web import build_registry as build_fetch_web_registry
from .run_cli import build_registry as build_run_cli_registry
from .file_tools import build_registry as build_file_registry
from .skill_tools import (
    build_registry as build_skill_registry,
    register_create_skill_tool,
    register_skill_tool,
)

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型提示
    from .skill_tools import CatalogSource

__all__ = [
    "ToolHandler",
    "ToolSpec",
    "ToolRegistry",
    "build_mermaid_registry",
    "build_datetime_registry",
    "build_web_search_registry",
    "build_fetch_web_registry",
    "build_run_cli_registry",
    "build_file_registry",
    "build_skill_registry",
    "register_create_skill_tool",
    "register_skill_tool",
    "register_recall_tool",
    "build_default_registry",
]


def build_default_registry(
    diagram_dir: str,
    *,
    search_api_key: str = "",
    search_timeout: int = 20,
    search_top_k: Optional[int] = None,
    search_snippet_chars: Optional[int] = None,
    skill_catalog: Optional["CatalogSource"] = None,
    project_root: Optional[str] = None,
    skill_refresh: Optional[Callable[[], object]] = None,
    # -- [tools] 段参数（由 Settings 注入，默认值取 config.ini 模板）-------
    fetch_timeout: Optional[int] = None,
    fetch_max_bytes: Optional[int] = None,
    fetch_max_chars: Optional[int] = None,
    file_read_chars: Optional[int] = None,
    file_list_entries: Optional[int] = None,
    cli_timeout: Optional[int] = None,
    cli_max_timeout: Optional[int] = None,
    cli_max_chars: Optional[int] = None,
) -> ToolRegistry:
    """装配应用默认工具集。

    Mermaid 4 个 + 日期时间 1 个 + 网络搜索 1 个 + 网页抓取 1 个 +
    本地命令行 1 个 + 通用文件操作 1 个；传入 ``skill_catalog`` 时再注册
    ``loadSkill`` 与 ``createSKill``。

    :param diagram_dir: Mermaid 架构图默认输出目录（由配置注入）
    :param search_api_key: 百度千帆 API Key（由配置注入；为空时工具仍会注册，
                           调用时返回“未配置”的清晰错误）
    :param search_timeout: 网络搜索请求超时（秒）
    :param search_top_k: 模型未指定 ``top_k`` 时默认返回条数（1..50）
    :param search_snippet_chars: 单条搜索摘要最大字符数
    :param skill_catalog: 初始化时发现的 skill 集合；为 ``None`` 时不注册
                          skill 工具（保持旧调用方的工具集不变）
    :param project_root: 项目根目录（``file0ps`` 的路径边界，默认当前工作目录）
    :param skill_refresh: 创建 skill 后刷新 skill 清单 / System Prompt 的回调
    :param fetch_timeout: fetchWeb 下载超时（秒）
    :param fetch_max_bytes: fetchWeb 单页 HTML 上限（字节）
    :param fetch_max_chars: fetchWeb 返回正文上限（字符）
    :param file_read_chars: file0ps 单次读取上限（字符）
    :param file_list_entries: file0ps list 最多展示条目数
    :param cli_timeout: RunCli 默认超时（秒）
    :param cli_max_timeout: RunCli 最大超时（秒）
    :param cli_max_chars: RunCli 输出截断长度（字符）
    """
    tools_cfg = DEFAULT_TOOLS  # 单一事实来源：config.ini 模板

    def _int(value: Optional[int], key: str) -> int:
        return int(value) if value is not None else int(float(tools_cfg[key]))

    def _search(value: Optional[int], key: str) -> int:
        return value if value is not None else int(float(DEFAULT_SEARCH[key]))

    registry = build_mermaid_registry(diagram_dir)
    for spec in _specs(build_datetime_registry()):
        registry.register(spec)
    for spec in _specs(
        build_web_search_registry(
            api_key=search_api_key,
            timeout=search_timeout,
            default_top_k=_search(search_top_k, "default_top_k"),
            snippet_chars=_search(search_snippet_chars, "max_snippet_chars"),
        )
    ):
        registry.register(spec)
    for spec in _specs(build_fetch_web_registry(
        timeout=_int(fetch_timeout, "fetch_timeout"),
        max_chars=_int(fetch_max_chars, "fetch_max_chars"),
        max_bytes=_int(fetch_max_bytes, "fetch_max_bytes"),
    )):
        registry.register(spec)
    for spec in _specs(build_run_cli_registry(
        timeout=_int(cli_timeout, "cli_timeout"),
        max_timeout=_int(cli_max_timeout, "cli_max_timeout"),
        max_chars=_int(cli_max_chars, "cli_max_chars"),
    )):
        registry.register(spec)
    for spec in _specs(build_file_registry(
        project_root,
        max_read_chars=_int(file_read_chars, "file_read_chars"),
        max_list_entries=_int(file_list_entries, "file_list_entries"),
    )):
        registry.register(spec)
    if skill_catalog is not None:
        register_skill_tool(registry, skill_catalog)
        register_create_skill_tool(registry, skill_catalog, skill_refresh)
    return registry


def _specs(registry: ToolRegistry):
    """取出注册表中全部 ToolSpec（供组合注册复用）。"""
    return [registry.get(name) for name in registry.names()]
