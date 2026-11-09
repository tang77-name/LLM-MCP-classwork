# -*- coding: utf-8 -*-
"""``.mcp.json`` 配置解析（MCP Server 连接信息的唯一来源）。

配置采用与主流 MCP 客户端一致的结构::

    {
      "mcpServers": {
        "Bazi-MCP": {
          "type": "streamable_http",
          "url": "https://example.com/mcp"
        },
        "local-demo": {
          "command": "python",
          "args": ["-m", "demo_server"],
          "env": {"API_TOKEN": "..."}
        }
      }
    }

约定：

- ``type`` 可省略：条目含 ``url`` 视为 ``streamable_http``，
  含 ``command`` 视为 ``stdio``；
- ``type`` 支持常见写法别名（``streamable-http`` / ``http`` 等）；
- 每个 server 可选 ``timeout``（秒，工具调用超时）、``headers``
  （StreamableHTTP 请求头，如 Authorization）、``cwd``（stdio 工作目录）。

本模块只做数据解析（纯函数，无 I/O 副作用），错误一律转成中文提示文本
返回给调用方，由调用方决定如何展示，保证单个配置问题不中断启动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 项目根目录下的 MCP 配置文件名
MCP_CONFIG_FILENAME = ".mcp.json"

#: 传输协议：标准输入输出（子进程）
TRANSPORT_STDIO = "stdio"
#: 传输协议：Streamable HTTP
TRANSPORT_STREAMABLE_HTTP = "streamable_http"

#: 常见写法 -> 内部统一的传输标识
_TRANSPORT_ALIASES: Dict[str, str] = {
    "stdio": TRANSPORT_STDIO,
    "standard_io": TRANSPORT_STDIO,
    "standard-io": TRANSPORT_STDIO,
    "subprocess": TRANSPORT_STDIO,
    "streamable_http": TRANSPORT_STREAMABLE_HTTP,
    "streamable-http": TRANSPORT_STREAMABLE_HTTP,
    "streamablehttp": TRANSPORT_STREAMABLE_HTTP,
    "streamable http": TRANSPORT_STREAMABLE_HTTP,
    "http": TRANSPORT_STREAMABLE_HTTP,
    "http_streamable": TRANSPORT_STREAMABLE_HTTP,
    "remote": TRANSPORT_STREAMABLE_HTTP,
}


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP Server 的连接描述（已规范化）。"""

    name: str
    transport: str  # TRANSPORT_STDIO / TRANSPORT_STREAMABLE_HTTP
    #: stdio：启动子进程的可执行文件
    command: str = ""
    #: stdio：命令行参数
    args: Tuple[str, ...] = ()
    #: stdio：附加环境变量（会与默认安全环境变量合并）
    env: Dict[str, str] = field(default_factory=dict)
    #: stdio：子进程工作目录
    cwd: str = ""
    #: streamable_http：服务端地址
    url: str = ""
    #: streamable_http：额外请求头（Authorization 等）
    headers: Dict[str, str] = field(default_factory=dict)
    #: 工具调用超时（秒；0 表示用客户端默认值）
    timeout: float = 0.0

    @property
    def target(self) -> str:
        """状态展示用的连接目标。"""
        if self.transport == TRANSPORT_STDIO:
            parts = [self.command, *self.args]
            return " ".join(str(p) for p in parts if p)
        return self.url

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.transport,
            "target": self.target,
            "timeout": self.timeout,
        }


def _as_str_dict(value: object, field_name: str) -> Dict[str, str]:
    """把配置中的映射规范成 ``{str: str}``（非法时抛 ValueError）。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"字段 {field_name} 必须是对象（JSON object）")
    return {str(k): "" if v is None else str(v) for k, v in value.items()}


def _string_list(value: object, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise ValueError(f"字段 {field_name} 必须是字符串数组")


def normalize_transport(raw: object) -> str:
    """把配置里的 ``type`` 归一化为内部传输标识。"""
    key = str(raw or "").strip().lower()
    if key in _TRANSPORT_ALIASES:
        return _TRANSPORT_ALIASES[key]
    supported = "stdio / streamable_http"
    raise ValueError(f"不支持的 MCP 传输类型 {raw!r}（支持：{supported}）")


def parse_server(name: str, entry: object) -> MCPServerConfig:
    """解析单个 server 条目；非法时抛出 :class:`ValueError`（中文提示）。"""
    if not isinstance(entry, dict):
        raise ValueError("server 配置必须是 JSON 对象")

    raw_type = entry.get("type") or entry.get("transport")
    url = str(entry.get("url") or "").strip()
    command = str(entry.get("command") or "").strip()

    if raw_type:
        transport = normalize_transport(raw_type)
    elif url:
        transport = TRANSPORT_STREAMABLE_HTTP
    elif command:
        transport = TRANSPORT_STDIO
    else:
        raise ValueError("缺少连接信息：stdio 需要 command，streamable_http 需要 url")

    timeout_raw = entry.get("timeout", 0)
    try:
        timeout = float(timeout_raw or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timeout 必须是数字（秒）-> {timeout_raw!r}") from exc
    if timeout < 0:
        raise ValueError(f"timeout 不能为负数 -> {timeout_raw!r}")

    if transport == TRANSPORT_STDIO:
        if not command:
            raise ValueError("stdio 类型缺少 command 字段")
        return MCPServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=_string_list(entry.get("args"), "args"),
            env=_as_str_dict(entry.get("env"), "env"),
            cwd=str(entry.get("cwd") or "").strip(),
            timeout=timeout,
        )

    if not url:
        raise ValueError("streamable_http 类型缺少 url 字段")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"url 必须以 http:// 或 https:// 开头 -> {url}")
    return MCPServerConfig(
        name=name,
        transport=transport,
        url=url,
        headers=_as_str_dict(entry.get("headers"), "headers"),
        timeout=timeout,
    )


def parse_config(text: str) -> Tuple[Dict[str, MCPServerConfig], Optional[str]]:
    """解析 ``.mcp.json`` 文本。

    :returns: ``(服务器表, 错误信息)``；配置不存在或为空时返回空表。
    """
    raw = (text or "").strip()
    if not raw:
        return {}, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f".mcp.json 不是合法 JSON -> 第 {exc.lineno} 行 {exc.msg}"

    if not isinstance(data, dict):
        return {}, ".mcp.json 顶层必须是 JSON 对象"

    # 兼容两种写法：标准的 {"mcpServers": {...}} 与直接平铺 server 表
    block = data.get("mcpServers", data)
    if not isinstance(block, dict):
        return {}, ".mcp.json 的 mcpServers 必须是 JSON 对象"
    block = {k: v for k, v in block.items() if k != "version"}
    if not block:
        return {}, None

    servers: Dict[str, MCPServerConfig] = {}
    errors: List[str] = []
    for name, entry in block.items():
        try:
            servers[str(name)] = parse_server(str(name), entry)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")

    if errors:
        return servers, "部分 MCP Server 配置无效 -> " + "；".join(errors)
    return servers, None


def load_mcp_config(
    path: Path | str,
) -> Tuple[Dict[str, MCPServerConfig], Optional[str]]:
    """读取并解析配置文件。

    文件不存在不是错误（返回空表）；读取失败 / JSON 非法返回错误文本，
    调用方据此输出提示，不影响本地工具。
    """
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"读取 {config_path.name} 失败 -> {exc}"
    except UnicodeDecodeError:
        return {}, f"{config_path.name} 不是 UTF-8 文本文件"
    return parse_config(text)


__all__ = [
    "MCP_CONFIG_FILENAME",
    "TRANSPORT_STDIO",
    "TRANSPORT_STREAMABLE_HTTP",
    "MCPServerConfig",
    "load_mcp_config",
    "normalize_transport",
    "parse_config",
    "parse_server",
]
