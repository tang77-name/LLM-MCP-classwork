# -*- coding: utf-8 -*-
"""单个 MCP Server 的连接封装（MCP Python SDK 标准客户端接口）。

一个 :class:`MCPServerConnection` 对应一条长连接：

1. ``stdio``         -> :func:`mcp.client.stdio.stdio_client`（拉起子进程）
2. ``streamable_http`` -> :func:`mcp.client.streamable_http.streamable_http_client`

连接建立后完成 ``initialize`` 握手并调用 ``list_tools()`` 缓存工具清单，
之后复用同一条会话把 ``call_tool`` 转发给 Server。

本模块只在事件循环线程内被调用（由 :class:`MCPManager` 调度），
不做任何打印，也不同步阻塞。同时兼容 MCP SDK 1.x / 2.x 的接口差异。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import TRANSPORT_STDIO, MCPServerConfig


#: 不应被业务逻辑吞掉的进程级异常
_FATAL = (KeyboardInterrupt, SystemExit)


class MCPTransportError(RuntimeError):
    """传输层 / 协议层失败（连接断开、握手失败等）。"""


@dataclass(frozen=True)
class MCPToolInfo:
    """归一化后的 MCP 工具描述（名称 + 描述 + 参数 Schema）。"""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


def _tool_input_schema(tool: Any) -> dict:
    """兼容 1.x ``inputSchema`` 与 2.x ``input_schema`` 命名差异。"""
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    if schema.get("type") not in (None, "object"):
        # Function Calling 要求 parameters 是 object 结构
        return {"type": "object", "properties": {}}
    return dict(schema)


def _normalize_tool(tool: Any) -> MCPToolInfo:
    name = str(getattr(tool, "name", "") or "").strip()
    description = str(getattr(tool, "description", "") or "").strip()
    return MCPToolInfo(
        name=name,
        description=description,
        input_schema=_tool_input_schema(tool),
    )


# -- 工具执行结果 -> 回传给 LLM 的文本 ---------------------------------------
def _block_to_text(block: Any) -> str:
    """把单个 ContentBlock 转成可读文本（图片等二进制内容只给摘要）。"""
    kind = str(getattr(block, "type", "") or "")
    if kind == "text":
        return str(getattr(block, "text", "") or "")
    if kind in ("image", "audio"):
        data = str(getattr(block, "data", "") or "")
        mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", "") or "未知类型"
        return f"[{kind} 内容：{mime}，base64 {len(data)} 字符]"
    if kind == "resource":
        resource = getattr(block, "resource", None)
        text = getattr(resource, "text", None)
        if text:
            return str(text)
        uri = getattr(resource, "uri", None)
        return f"[资源内容：{uri}]" if uri else "[资源内容]"
    if kind == "resource_link":
        return f"[资源链接：{getattr(block, 'uri', '')} {getattr(block, 'name', '')}]".strip()
    # 未知块类型：尽量序列化
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            return json.dumps(dump(mode="json"), ensure_ascii=False)
        except Exception:  # noqa: BLE001 - 序列化失败退回 str
            pass
    return str(block)


def _structured_json(structured: Any) -> Optional[str]:
    """结构化结果序列化为 JSON 文本。"""
    if structured is None:
        return None
    try:
        return json.dumps(structured, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 罕见的不可序列化值
        return f"（结构化数据序列化失败：{exc}）原始值：{structured!r}"


def format_tool_result(result: Any, *, label: str) -> str:
    """把 MCP ``CallToolResult`` 规范成回传给模型的字符串。

    覆盖三类返回：纯文本内容、结构化数据（``structuredContent``）、
    工具侧错误（``isError``）；同时兼容 ``content`` / ``structured_content``
    两种字段命名，以及协议级异常对象。
    """
    if result is None:
        return f"（MCP 工具 {label} 未返回内容）"

    is_error = bool(getattr(result, "is_error", None) or getattr(result, "isError", False))
    blocks = getattr(result, "content", None)
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)

    if blocks is None and structured is None:
        # 非 CallToolResult（协议级 Result / 输入请求等）：直接给出可读表示
        dump = getattr(result, "model_dump", None)
        if callable(dump):
            try:
                return json.dumps(dump(mode="json"), ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001
                pass
        return str(result)

    parts: List[str] = []
    for block in blocks or []:
        text = _block_to_text(block)
        if text:
            parts.append(text)
    body = "\n".join(part for part in parts if part).strip()

    structured_text = _structured_json(structured)
    if structured_text:
        # 内容里已包含同样的 JSON 时不重复输出
        compact = json.dumps(structured, ensure_ascii=False, default=str)
        if compact not in body and structured_text not in body:
            body = f"{body}\n\n结构化数据(JSON)：\n{structured_text}" if body else (
                f"结构化数据(JSON)：\n{structured_text}"
            )

    if is_error:
        detail = body or "工具执行失败（未返回错误详情）"
        return f"错误：MCP 工具 {label} 执行失败 → {detail}"
    return body or f"（MCP 工具 {label} 返回空结果）"


class MCPServerConnection:
    """一个 MCP Server 的长连接（握手 + 工具清单 + 转发调用）。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.tools: List[MCPToolInfo] = []
        self._stack = AsyncExitStack()
        self._session: Any = None
        self._closed = False
        #: 关闭传输层时捕获到的根因错误文本（连接失败排查用）
        self.close_error: str = ""
        #: stdio 的 stderr 兜底文件（sys.__stderr__ 不可用时使用，防止被 GC 关闭）
        self._errlog_fallback: Any = None

    # -- 状态 ---------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return self._session is not None and not self._closed

    # -- 连接 ---------------------------------------------------------------
    def _transport(self) -> Any:
        """按配置构造 SDK 传输层的异步上下文管理器。"""
        if self.config.transport == TRANSPORT_STDIO:
            return self._stdio_transport()
        return self._http_transport()

    def _stdio_transport(self) -> Any:
        try:
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise MCPTransportError(
                "当前 mcp SDK 缺少 stdio 客户端（请升级：pip install -U mcp）"
            ) from exc

        env: Dict[str, str] = {}
        try:
            # 默认只继承安全变量（PATH 等），再叠加配置里的 env
            from mcp.client.stdio import get_default_environment

            env.update(get_default_environment())
        except Exception:  # noqa: BLE001 - SDK 版本差异，退回当前进程环境
            env.update({k: v for k, v in __import__("os").environ.items()})
        env.update(self.config.env)

        values = {
            "command": self.config.command,
            "args": list(self.config.args),
            "env": env,
            "cwd": self.config.cwd or None,
        }
        # 兼容不同 SDK 版本的字段集（老版本可能没有 cwd）
        fields = getattr(StdioServerParameters, "model_fields", {}) or {}
        params = StdioServerParameters(
            **{k: v for k, v in values.items() if not fields or k in fields}
        )
        kwargs = {}
        if "errlog" in inspect.signature(stdio_client).parameters:
            # 注意：不能用 sys.stderr —— Textual（尤其 run_test）会把 sys.stderr
            # 重定向为无 fileno 的对象，导致 anyio 启动子进程时报
            # “Bad file descriptor”。sys.__stderr__ 始终指向真实终端。
            errlog = getattr(sys, "__stderr__", None)
            if errlog is None or not hasattr(errlog, "fileno"):
                self._errlog_fallback = open(
                    os.devnull, "w", encoding="utf-8", errors="replace"
                )
                errlog = self._errlog_fallback
            kwargs["errlog"] = errlog
        return stdio_client(params, **kwargs)

    def _http_transport(self) -> Any:
        try:
            from mcp.client.streamable_http import streamable_http_client as client_fn
        except ImportError:  # MCP SDK 1.x 的函数名
            try:
                from mcp.client.streamable_http import streamablehttp_client as client_fn
            except ImportError as exc:  # pragma: no cover - 依赖缺失
                raise MCPTransportError(
                    "当前 mcp SDK 缺少 streamable_http 客户端"
                    "（请升级：pip install -U mcp）"
                ) from exc

        headers = dict(self.config.headers)
        try:
            signature = inspect.signature(client_fn)
        except (TypeError, ValueError):  # pragma: no cover - 内建函数兜底
            signature = None

        if headers and signature is not None and "headers" in signature.parameters:
            return client_fn(self.config.url, headers=headers)  # SDK 1.x

        if headers:
            # SDK 2.x 通过 httpx 客户端注入请求头
            client = self._build_http_client(headers)
            if client is None:
                raise MCPTransportError(
                    "当前 mcp SDK 不支持配置 headers（缺少 httpx 依赖）"
                )
            self._stack.push_async_callback(_aclose_quietly, client)
            return client_fn(self.config.url, http_client=client)

        return client_fn(self.config.url)

    @staticmethod
    def _build_http_client(headers: Dict[str, str]) -> Any:
        for module_name in ("httpx2", "httpx"):
            try:
                module = __import__(module_name)
            except ImportError:
                continue
            return module.AsyncClient(headers=headers)
        return None

    async def connect(self) -> None:
        """打开传输层 -> ``initialize`` 握手 -> ``list_tools()`` 拉取工具清单。

        :raises MCPTransportError: 任何一步失败（含超时 / 非 200 / 握手异常）。
        """
        try:
            streams = await self._stack.enter_async_context(self._transport())
            read_stream, write_stream = _unpack_streams(streams)
        except MCPTransportError:
            raise
        except _FATAL:  # pragma: no cover - 进程级异常直接上抛
            raise
        except BaseException as exc:  # noqa: BLE001 - SDK 内部异常（含异常组）
            raise MCPTransportError(
                f"连接 {self.config.target} 失败 → {_error_text(exc)}"
            ) from exc

        try:
            from mcp import ClientSession
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise MCPTransportError(
                "未安装 mcp SDK，请执行：pip install mcp"
            ) from exc

        session = ClientSession(read_stream, write_stream)
        try:
            self._session = await self._stack.enter_async_context(session)
        except _FATAL:  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001
            raise MCPTransportError(f"会话启动失败 → {_error_text(exc)}") from exc

        try:
            await self._session.initialize()
        except _FATAL:  # pragma: no cover
            raise
        except BaseException as exc:  # noqa: BLE001
            raise MCPTransportError(
                f"MCP 握手（initialize）失败 → {_error_text(exc)}"
            ) from exc

        self.tools = await self._list_tools()

    async def _list_tools(self) -> List[MCPToolInfo]:
        """调用 ``list_tools()``（SDK 1.x/2.x 分页参数不同，做了兼容）。"""
        session = self._session
        list_tools = session.list_tools
        params_style = _list_tools_param_style(list_tools)

        tools: List[MCPToolInfo] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        while True:
            kwargs: Dict[str, Any] = {}
            if cursor:
                if params_style == "cursor":
                    kwargs["cursor"] = cursor
                else:
                    kwargs["params"] = _paginated_params(cursor)
            try:
                result = await list_tools(**kwargs)
            except _FATAL:  # pragma: no cover
                raise
            except BaseException as exc:  # noqa: BLE001
                if not tools:
                    raise MCPTransportError(
                        f"list_tools() 调用失败 → {_error_text(exc)}"
                    ) from exc
                break  # 分页中途失败：保留已拿到的工具

            for tool in getattr(result, "tools", None) or []:
                info = _normalize_tool(tool)
                if info.name:
                    tools.append(info)

            next_cursor = getattr(result, "nextCursor", None)
            if next_cursor is None:
                next_cursor = getattr(result, "next_cursor", None)
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = str(next_cursor)
        return tools

    # -- 调用 ---------------------------------------------------------------
    async def call_tool(self, name: str, arguments: Optional[dict]) -> Any:
        """把工具调用转发给 MCP Server，返回原始 ``CallToolResult``。"""
        if not self.connected:
            raise MCPTransportError(f"MCP 服务器 {self.name} 未连接")
        return await self._session.call_tool(name, dict(arguments or {}))

    async def close(self) -> None:
        """关闭会话与传输层（stdio 子进程会被一并回收）。"""
        if self._closed:
            return
        self._closed = True
        self._session = None
        try:
            await self._stack.aclose()
        except _FATAL:  # pragma: no cover - 进程级异常
            raise
        except BaseException as exc:  # noqa: BLE001 - 关闭失败（含异常组）不上传
            # SDK 常在关闭阶段抛出真正的根因（如连接被拒绝），留作诊断信息
            self.close_error = _error_text(exc)


# -- 辅助 -------------------------------------------------------------------
def _error_text(exc: BaseException) -> str:
    """异常（含 ExceptionGroup）-> 单行可读文本。"""
    leaves = _leaves(exc)
    useful = [
        leaf
        for leaf in leaves
        if not isinstance(leaf, (asyncio.CancelledError, GeneratorExit))
    ]
    parts = []
    for leaf in (useful or leaves)[:3]:
        text = str(leaf).strip()
        parts.append(text if text else type(leaf).__name__)
    return "；".join(parts) or type(exc).__name__


def _leaves(exc: BaseException) -> List[BaseException]:
    children = getattr(exc, "exceptions", None)
    if isinstance(children, (list, tuple)):
        result: List[BaseException] = []
        for child in children:
            result.extend(_leaves(child))
        return result
    return [exc]


def _unpack_streams(streams: Any) -> tuple:
    """兼容 SDK 返回 ``(read, write)`` 元组或带属性的对象。"""
    if isinstance(streams, tuple) and len(streams) == 2:
        return streams[0], streams[1]
    read = getattr(streams, "read_stream", None) or getattr(streams, "receive_stream", None)
    write = getattr(streams, "write_stream", None) or getattr(streams, "send_stream", None)
    if read is None or write is None:
        raise MCPTransportError("MCP 传输层返回了无法识别的流对象")
    return read, write


def _list_tools_param_style(list_tools) -> str:
    """判断 ``list_tools`` 的分页参数写法（1.x: cursor / 2.x: params）。"""
    try:
        params = inspect.signature(list_tools).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return "params"
    return "cursor" if "cursor" in params else "params"


def _paginated_params(cursor: str) -> Any:
    try:
        from mcp.types import PaginatedRequestParams
    except ImportError:  # pragma: no cover - 兼容极老版本
        return {"cursor": cursor}
    try:
        return PaginatedRequestParams(cursor=cursor)
    except Exception:  # noqa: BLE001 - pydantic 校验失败退回裸字典
        return {"cursor": cursor}


async def _aclose_quietly(client: Any) -> None:
    """尽力关闭外部注入的 HTTP 客户端（失败不向上传播）。"""
    aclose = getattr(client, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "MCPToolInfo",
    "MCPTransportError",
    "MCPServerConnection",
    "format_tool_result",
]
