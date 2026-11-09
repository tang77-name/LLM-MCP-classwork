# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）客户端接入层。

对外只暴露三样东西：

- :func:`load_mcp_config`   读取项目根目录的 ``.mcp.json``
- :class:`MCPManager`       连接 Server、注册工具、转发调用、状态查询
- :class:`MCPServerStatus`  单个 Server 的连接状态（前端错误展示）

用法（由 ``AgentService`` 在启动时调用）::

    mcp = MCPManager(project_root / ".mcp.json")
    mcp.connect(registry)     # 工具直接并入现有 ToolRegistry
    ...
    mcp.close()

依赖方向：``core.mcp -> core.tools / mcp SDK``，不依赖任何前端。
"""

from __future__ import annotations

from .client import MCPToolInfo, MCPTransportError, MCPServerConnection, format_tool_result
from .config import (
    MCP_CONFIG_FILENAME,
    TRANSPORT_STDIO,
    TRANSPORT_STREAMABLE_HTTP,
    MCPServerConfig,
    load_mcp_config,
    parse_config,
)
from .manager import (
    DEFAULT_CALL_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    MCPManager,
    MCPServerStatus,
    sanitize_tool_name,
)

__all__ = [
    "MCP_CONFIG_FILENAME",
    "TRANSPORT_STDIO",
    "TRANSPORT_STREAMABLE_HTTP",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPManager",
    "MCPToolInfo",
    "MCPTransportError",
    "MCPServerConnection",
    "DEFAULT_CALL_TIMEOUT",
    "DEFAULT_CONNECT_TIMEOUT",
    "format_tool_result",
    "load_mcp_config",
    "parse_config",
    "sanitize_tool_name",
]
