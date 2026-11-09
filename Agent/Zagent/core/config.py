# -*- coding: utf-8 -*-
"""配置加载。

优先级（高 -> 低）：

1. 环境变量 ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``CHAT_MODEL``、
   ``BAIDU_QIANFAN_API_KEY`` 以及 ``ZAGENT_*`` 系列变量；
2. 配置文件（``ZAGENT_CONFIG`` 指定 > ``--config`` 传入 > 当前工作目录
   ``config.ini`` > 项目根目录 ``config.ini``）的 ``[llm]`` / ``[agent]`` /
   ``[search]`` / ``[web]`` / ``[tools]`` / ``[mcp]`` 段；
3. 内置默认值（DeepSeek 官方接口）。

首次运行（当前目录找不到任何 config.ini，且未显式指定配置文件）时，
会自动在当前目录生成一份**完整的带 mock 信息的 config.ini**，并在
``Settings.startup_notice`` 中返回提示文案，由前端弹窗告知用户
“修改配置后重新启动才能正常工作”。

配置对象为不可变 dataclass，加载一次后在整个进程内只读共享。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from .errors import ConfigError

#: 项目根目录（core 包的上一级，即 Zagent 目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LLM = {
    "base_url": "https://api.deepseek.com",
    # 出于安全不硬编码真实 key：请写在 config.ini 或环境变量中
    "api_key": "EMPTY",
    "model_name": "deepseek-chat",
}

DEFAULT_AGENT = {
    "temperature": "0.7",
    "timeout": "60",
    "max_tool_rounds": "8",
    "diagram_dir": "",  # 空 -> 运行时取 <cwd>/diagrams
    "market_dir": "",   # 空 -> 运行时取 <cwd>/market
    # 模型上下文窗口（token）：0 / 空 = 未知 -> 使用默认 128k（UI 标注“默认”）
    "context_window": "0",
}

DEFAULT_SEARCH = {
    # 百度千帆 API Key：仅用于 searchWeb 联网搜索
    "baidu_qianfan_api_key": "",
    "timeout": "20",
    # 模型未指定 top_k 时默认返回的网页条数（1..50）
    "default_top_k": "10",
    # 单条搜索摘要的最大字符数（控制上下文占用）
    "max_snippet_chars": "600",
}

DEFAULT_WEB = {
    "host": "127.0.0.1",
    "port": "8787",
    "open_browser": "1",
}

#: 工具行为参数（[tools] 段）：留空/缺失时按这里的默认值执行
DEFAULT_TOOLS = {
    "fetch_timeout": "30",        # fetchWeb 下载超时（秒）
    "fetch_max_bytes": "8388608", # fetchWeb 单页 HTML 上限（字节，默认 8MB）
    "fetch_max_chars": "20000",   # fetchWeb 返回正文上限（字符）
    "file_read_chars": "8000",    # file0ps 单次读取上限（字符）
    "file_list_entries": "500",   # file0ps list 最多展示条目数
    "cli_timeout": "60",          # RunCli 默认执行超时（秒）
    "cli_max_timeout": "600",     # RunCli 允许的最大超时（秒）
    "cli_max_chars": "8000",      # RunCli 输出截断长度（字符）
    "recall_limit": "5",          # recallContext 返回条数
    "recall_max_chars": "3000",   # recallContext 结果长度上限（字符）
}

#: MCP 客户端参数（[mcp] 段）
DEFAULT_MCP = {
    "connect_timeout": "20",  # 单个 Server 连接超时（秒）
    "call_timeout": "60",     # 工具调用超时（秒）
}

#: 首次运行自动生成 config.ini 的完整模板（含 mock 信息）
DEFAULT_CONFIG_TEXT = """\
# =============================================================================
# Zagent 配置文件（首次运行自动生成）
#
# 使用说明：
#   1. 把下面标有「模拟」的值替换为你的真实信息；
#   2. 修改完成后重新启动 Zagent 才能正常工作；
#   3. 删除本文件再次启动会重新生成一份默认配置。
# =============================================================================

[llm]
# 模型服务地址（OpenAI 兼容接口）
base_url = https://api.deepseek.com
# 模型 API Key（模拟）：请替换为你的 key，否则无法调用模型
api_key = sk-mock-please-replace-me
# 模型名称
model_name = deepseek-chat

[agent]
# 采样温度 0~2
temperature = 0.7
# 单次请求超时（秒）
timeout = 60
# 最大工具调用轮数
max_tool_rounds = 8
# 架构图输出目录（留空 = <当前目录>/diagrams）
diagram_dir =
# 技能市场目录（留空 = <当前目录>/market）
market_dir =
# 模型上下文窗口（token）；0 = 未知，界面按默认 128k 标注
context_window = 0

[search]
# 百度千帆 API Key（模拟）：仅 searchWeb 联网搜索需要
baidu_qianfan_api_key = bce-v3-mock-please-replace-me
# 搜索超时（秒）
timeout = 20
# 模型未指定 top_k 时默认返回的网页条数（1..50）
default_top_k = 10
# 单条搜索摘要的最大字符数
max_snippet_chars = 600

[web]
# Web 前端监听地址与端口
host = 127.0.0.1
port = 8787
# 启动后是否自动打开浏览器（1/0）
open_browser = 1

[tools]
# fetchWeb：下载超时（秒）
fetch_timeout = 30
# fetchWeb：单页 HTML 下载上限（字节，默认 8MB）
fetch_max_bytes = 8388608
# fetchWeb：返回正文最大字符数
fetch_max_chars = 20000
# file0ps：单次读取最大字符数
file_read_chars = 8000
# file0ps：list 最多展示条目数
file_list_entries = 500
# RunCli：默认执行超时（秒）
cli_timeout = 60
# RunCli：允许的最大超时（秒）
cli_max_timeout = 600
# RunCli：输出截断长度（字符）
cli_max_chars = 8000
# recallContext：返回条数
recall_limit = 5
# recallContext：结果长度上限（字符）
recall_max_chars = 3000

[mcp]
# 单个 MCP Server 连接超时（秒）
connect_timeout = 20
# MCP 工具调用超时（秒）
call_timeout = 60
"""


def write_default_config(target: Path) -> Path:
    """把默认配置模板写入 ``target``（已存在时不做任何修改）。

    :return: 实际写入的文件路径
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return target


@dataclass(frozen=True)
class Settings:
    """运行期配置（只读）。"""

    base_url: str
    api_key: str
    model_name: str
    temperature: float = 0.7
    timeout: int = 60
    max_tool_rounds: int = 8
    diagram_dir: str = field(default="")
    market_dir: str = field(default="")
    baidu_qianfan_api_key: str = field(default="")
    search_timeout: int = 20
    #: searchWeb 未指定 top_k 时的默认条数（1..50）
    search_top_k: int = 10
    #: searchWeb 单条摘要最大字符数
    search_snippet_chars: int = 600
    #: 模型上下文窗口（token）；0 = 未知，运行时按默认 128k 处理并标注“默认”
    context_window: int = 0
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_open_browser: bool = True
    # -- [tools] 段：工具行为参数（均带默认值，见 DEFAULT_TOOLS）----------
    fetch_timeout: int = 30
    fetch_max_bytes: int = 8 * 1024 * 1024
    fetch_max_chars: int = 20000
    file_read_chars: int = 8000
    file_list_entries: int = 500
    cli_timeout: int = 60
    cli_max_timeout: int = 600
    cli_max_chars: int = 8000
    recall_limit: int = 5
    recall_max_chars: int = 3000
    # -- [mcp] 段：MCP 客户端参数 ----------------------------------------
    mcp_connect_timeout: int = 20
    mcp_call_timeout: int = 60
    #: 首次运行自动生成 config.ini 时的提示（非空表示需要弹窗告知用户）
    startup_notice: str = ""
    config_path: str = ""

    @property
    def chat_url(self) -> str:
        """Chat Completions 端点。"""
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def diagram_directory(self) -> str:
        """Mermaid 架构图默认输出目录（绝对路径）。"""
        base = self.diagram_dir.strip()
        if not base:
            base = os.path.join(os.getcwd(), "diagrams")
        return os.path.abspath(os.path.expanduser(base))

    @property
    def market_directory(self) -> str:
        """本地 Skill 市场目录（绝对路径）。"""
        base = self.market_dir.strip()
        if not base:
            base = os.path.join(os.getcwd(), "market")
        return os.path.abspath(os.path.expanduser(base))

    def masked(self) -> dict:
        """供 UI 展示的脱敏副本。"""
        key = self.api_key or ""
        shown = key if len(key) <= 8 else f"{key[:4]}***{key[-4:]}"
        search_key = self.baidu_qianfan_api_key or ""
        search_shown = (
            search_key
            if len(search_key) <= 8
            else f"{search_key[:8]}***{search_key[-4:]}"
        )
        return {
            "base_url": self.base_url,
            "api_key": shown,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_tool_rounds": self.max_tool_rounds,
            "diagram_dir": self.diagram_directory,
            "market_dir": self.market_directory,
            "search_api_key": search_shown,
            "search_configured": bool(search_key.strip()),
            "search_timeout": self.search_timeout,
            "search_top_k": self.search_top_k,
            "search_snippet_chars": self.search_snippet_chars,
            "context_window": self.context_window,
            "web_host": self.web_host,
            "web_port": self.web_port,
            "web_open_browser": self.web_open_browser,
            "config_path": self.config_path,
            "tool_params": {
                "fetch_timeout": self.fetch_timeout,
                "fetch_max_bytes": self.fetch_max_bytes,
                "fetch_max_chars": self.fetch_max_chars,
                "file_read_chars": self.file_read_chars,
                "file_list_entries": self.file_list_entries,
                "cli_timeout": self.cli_timeout,
                "cli_max_timeout": self.cli_max_timeout,
                "cli_max_chars": self.cli_max_chars,
                "recall_limit": self.recall_limit,
                "recall_max_chars": self.recall_max_chars,
            },
            "mcp_params": {
                "connect_timeout": self.mcp_connect_timeout,
                "call_timeout": self.mcp_call_timeout,
            },
        }


def _candidate_paths(explicit: Optional[str]) -> list[Path]:
    """按优先级返回待解析的配置文件路径列表。"""
    candidates: list[Path] = []
    env_path = os.environ.get("ZAGENT_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.cwd() / "config.ini")
    candidates.append(PROJECT_ROOT / "config.ini")
    return candidates


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:  # 文件损坏不应让进程崩溃，降级为警告
        raise ConfigError(f"解析配置文件失败 -> {path}: {exc}") from exc
    return parser


def load_settings(config_path: Optional[str] = None) -> Settings:
    """读取配置：环境变量 > config.ini > 内置默认值。

    :raises ConfigError: 显式指定的配置文件不存在或无法解析。
    """
    llm = dict(DEFAULT_LLM)
    agent = dict(DEFAULT_AGENT)
    search = dict(DEFAULT_SEARCH)
    web = dict(DEFAULT_WEB)
    tools = dict(DEFAULT_TOOLS)
    mcp = dict(DEFAULT_MCP)
    used_path = ""
    startup_notice = ""

    paths = _candidate_paths(config_path)
    explicit = bool(config_path) or bool(
        os.environ.get("ZAGENT_CONFIG", "").strip()
    )
    cwd_config = Path.cwd() / "config.ini"
    if not explicit and not cwd_config.is_file():
        # 首次运行：当前目录没有 config.ini -> 生成一份完整的 mock 配置，
        # 并通过 startup_notice 让前端弹窗提示用户修改后重启。
        try:
            created = write_default_config(cwd_config)
        except OSError as exc:
            startup_notice = (
                f"无法在当前目录生成 config.ini（{exc}），"
                "已临时使用内置默认配置。"
            )
        else:
            startup_notice = (
                f"已在当前目录生成配置文件：{created}\n\n"
                "文件中的 api_key、搜索 Key 等均为【模拟信息】，"
                "请用真实值替换后重新启动 Zagent，\n"
                "否则无法正常调用模型与联网搜索。"
            )
            paths = [created]

    for path in paths:
        if not path.is_file():
            if config_path and str(path) == str(Path(config_path)):
                raise ConfigError(f"指定的配置文件不存在 -> {path}")
            continue
        parser = _read_ini(path)
        if parser.has_section("llm"):
            for key in llm:
                if parser.has_option("llm", key):
                    llm[key] = parser.get("llm", key).strip()
        if parser.has_section("agent"):
            for key in agent:
                if parser.has_option("agent", key):
                    agent[key] = parser.get("agent", key).strip()
        if parser.has_section("search"):
            # 兼容两种键名：baidu_qianfan_api_key（推荐）与 baidu_qianfan_apikey
            search_key = parser.get(
                "search", "baidu_qianfan_api_key", fallback=""
            ).strip()
            if not search_key:
                search_key = parser.get(
                    "search", "baidu_qianfan_apikey", fallback=""
                ).strip()
            if search_key:
                search["baidu_qianfan_api_key"] = search_key
            if parser.has_option("search", "timeout"):
                search["timeout"] = parser.get("search", "timeout").strip()
            for key in ("default_top_k", "max_snippet_chars"):
                if parser.has_option("search", key):
                    search[key] = parser.get("search", key).strip()
        if parser.has_section("web"):
            for key in web:
                if parser.has_option("web", key):
                    web[key] = parser.get("web", key).strip()
        if parser.has_section("tools"):
            for key in tools:
                if parser.has_option("tools", key):
                    tools[key] = parser.get("tools", key).strip()
        if parser.has_section("mcp"):
            for key in mcp:
                if parser.has_option("mcp", key):
                    mcp[key] = parser.get("mcp", key).strip()
        used_path = str(path)
        break

    # 环境变量优先
    llm["api_key"] = os.environ.get("OPENAI_API_KEY", llm["api_key"])
    llm["base_url"] = os.environ.get("OPENAI_BASE_URL", llm["base_url"])
    llm["model_name"] = os.environ.get("CHAT_MODEL", llm["model_name"])
    agent["diagram_dir"] = os.environ.get("ZAGENT_DIAGRAM_DIR", agent["diagram_dir"])
    agent["market_dir"] = os.environ.get("ZAGENT_MARKET_DIR", agent["market_dir"])
    agent["temperature"] = os.environ.get("ZAGENT_TEMPERATURE", agent["temperature"])
    agent["timeout"] = os.environ.get("ZAGENT_TIMEOUT", agent["timeout"])
    agent["max_tool_rounds"] = os.environ.get(
        "ZAGENT_MAX_TOOL_ROUNDS", agent["max_tool_rounds"]
    )
    agent["context_window"] = os.environ.get(
        "ZAGENT_CONTEXT_WINDOW", agent["context_window"]
    )
    search["baidu_qianfan_api_key"] = os.environ.get(
        "BAIDU_QIANFAN_API_KEY", search["baidu_qianfan_api_key"]
    )
    search["timeout"] = os.environ.get("ZAGENT_SEARCH_TIMEOUT", search["timeout"])
    web["host"] = os.environ.get("ZAGENT_WEB_HOST", web["host"])
    web["port"] = os.environ.get("ZAGENT_WEB_PORT", web["port"])
    web["open_browser"] = os.environ.get(
        "ZAGENT_WEB_OPEN_BROWSER", web["open_browser"]
    )

    try:
        temperature = float(agent["temperature"])
        timeout = int(float(agent["timeout"]))
        max_tool_rounds = int(float(agent["max_tool_rounds"]))
        search_timeout = int(float(search["timeout"]))
        search_top_k = int(float(search["default_top_k"]))
        search_snippet_chars = int(float(search["max_snippet_chars"]))
        web_port = int(float(web["port"]))
        context_window = int(float(agent["context_window"] or 0))
    except ValueError as exc:
        raise ConfigError(f"[agent]/[search]/[web] 段数值格式非法: {exc}") from exc

    def _positive(section: str, key: str, raw: str) -> int:
        """解析 [tools]/[mcp] 的正整数参数（留空 -> 用内置默认值）。"""
        text = (raw or "").strip()
        if not text:
            return int(float(DEFAULT_TOOLS.get(key, DEFAULT_MCP.get(key, "1"))))
        try:
            value = int(float(text))
        except ValueError as exc:
            raise ConfigError(
                f"[{section}] {key} 数值格式非法 -> {raw!r}"
            ) from exc
        if value <= 0:
            raise ConfigError(f"[{section}] {key} 必须为正整数 -> {value}")
        return value

    tool_values = {
        key: _positive("tools", key, value) for key, value in tools.items()
    }
    mcp_values = {
        key: _positive("mcp", key, value) for key, value in mcp.items()
    }

    if temperature < 0 or temperature > 2:
        raise ConfigError(f"temperature 需在 [0, 2] 区间 -> {temperature}")
    if timeout <= 0:
        raise ConfigError(f"timeout 必须为正整数 -> {timeout}")
    if max_tool_rounds <= 0:
        raise ConfigError(f"max_tool_rounds 必须为正整数 -> {max_tool_rounds}")
    if search_timeout <= 0:
        raise ConfigError(f"[search] timeout 必须为正整数 -> {search_timeout}")
    if search_top_k < 1 or search_top_k > 50:
        raise ConfigError(
            f"[search] default_top_k 需在 [1, 50] 区间 -> {search_top_k}"
        )
    if search_snippet_chars <= 0:
        raise ConfigError(
            f"[search] max_snippet_chars 必须为正整数 -> {search_snippet_chars}"
        )
    if context_window < 0:
        raise ConfigError(
            f"[agent] context_window 不能为负数 -> {context_window}"
        )
    if web_port < 0 or web_port > 65535:
        raise ConfigError(f"[web] port 需在 [0, 65535] 区间 -> {web_port}")
    web_host = (web["host"] or "").strip() or "127.0.0.1"
    web_open_browser = str(web["open_browser"]).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
        "",
    )

    base_url = (llm["base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError("base_url 不能为空")

    return Settings(
        base_url=base_url,
        api_key=(llm["api_key"] or "").strip(),
        model_name=(llm["model_name"] or "").strip(),
        temperature=temperature,
        timeout=timeout,
        max_tool_rounds=max_tool_rounds,
        diagram_dir=(agent["diagram_dir"] or "").strip(),
        market_dir=(agent["market_dir"] or "").strip(),
        baidu_qianfan_api_key=(search["baidu_qianfan_api_key"] or "").strip(),
        search_timeout=search_timeout,
        search_top_k=search_top_k,
        search_snippet_chars=search_snippet_chars,
        context_window=context_window,
        web_host=web_host,
        web_port=web_port,
        web_open_browser=web_open_browser,
        fetch_timeout=tool_values["fetch_timeout"],
        fetch_max_bytes=tool_values["fetch_max_bytes"],
        fetch_max_chars=tool_values["fetch_max_chars"],
        file_read_chars=tool_values["file_read_chars"],
        file_list_entries=tool_values["file_list_entries"],
        cli_timeout=tool_values["cli_timeout"],
        cli_max_timeout=tool_values["cli_max_timeout"],
        cli_max_chars=tool_values["cli_max_chars"],
        recall_limit=tool_values["recall_limit"],
        recall_max_chars=tool_values["recall_max_chars"],
        mcp_connect_timeout=mcp_values["connect_timeout"],
        mcp_call_timeout=mcp_values["call_timeout"],
        startup_notice=startup_notice,
        config_path=used_path,
    )


def with_overrides(settings: Settings, **kwargs) -> Settings:
    """返回应用命令行覆盖项后的新配置（保持不可变语义）。"""
    return replace(settings, **kwargs)


__all__ = [
    "Settings",
    "load_settings",
    "with_overrides",
    "write_default_config",
    "DEFAULT_CONFIG_TEXT",
    "PROJECT_ROOT",
]
