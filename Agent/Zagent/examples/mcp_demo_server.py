# -*- coding: utf-8 -*-
"""最小可用的 stdio MCP Server 示例（用于验证 Zagent 的 stdio 传输）。

启动方式（由 ``.mcp.json`` 自动拉起，无需手动运行）::

    {
      "mcpServers": {
        "demo-stdio": {
          "type": "stdio",
          "command": "python",
          "args": ["examples/mcp_demo_server.py"]
        }
      }
    }

同时兼容 MCP SDK 1.x（``FastMCP``）与 2.x（``MCPServer``）。
"""

from __future__ import annotations

try:  # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer(
    "zagent-demo-stdio",
    instructions="Zagent MCP 集成自测用的演示服务器：提供加法与回显两个工具。",
)


@server.tool(description="把两个整数相加并返回结果。")
def demo_add(a: int, b: int) -> dict:
    """返回 ``{"sum": a + b}``，用于验证结构化数据的回传链路。"""
    return {"sum": a + b, "expression": f"{a} + {b}"}


@server.tool(description="原样返回传入的文本，用于验证文本内容回传。")
def demo_echo(text: str) -> str:
    return f"echo: {text}"


def main() -> int:
    # stdout 保留给 JSON-RPC 协议，日志一律走 stderr
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
