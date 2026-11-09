# -*- coding: utf-8 -*-
"""MCP 客户端管理器：连接全部 Server 并把工具并入现有工具注册表。

职责链::

    .mcp.json ──> 逐个连接（失败互相隔离）
                     │
                     ├─ list_tools() ──> 生成 ToolSpec 注册进 ToolRegistry
                     │                    （与 runCli / fetchWeb / loadSkill 共存）
                     └─ 维护 “LLM 工具名 -> (Server, 原始工具名)” 映射

    LLM 发起 Function Call ──> 按映射找到 Server ──> 事件循环线程内
    await call_tool() ──> 文本 / 结构化数据 / 错误信息统一转成字符串回传

设计要点：

- **每个 Server 一个常驻任务**：传输层的 ``__aenter__`` 与 ``__aexit__``
  永远在同一个任务里执行（MCP SDK 内部使用 anyio cancel scope，
  跨任务退出会报 "Attempted to exit cancel scope in a different task"）；
  调用通过任务内的消息队列转发，天然串行化单个连接的生命周期。
- 所有 MCP 通信跑在**独立的事件循环线程**里，同步的
  ``ToolHandler`` 通过 ``run_coroutine_threadsafe`` 提交任务，
  因此不会与 Web / TUI 的事件循环互相干扰；
- 单个 Server 连接失败只会生成一条状态记录（前端可见的错误信息），
  其余 Server 与本地工具照常工作；
- 工具名冲突时自动加服务器名前缀，映射表保证转发时还原为原始工具名。
"""

from __future__ import annotations

import asyncio
import atexit
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..tools import ToolRegistry, ToolSpec
from .client import (
    MCPToolInfo,
    MCPTransportError,
    MCPServerConnection,
    format_tool_result,
)
from .config import MCPServerConfig, load_mcp_config

#: 建立连接的默认超时（秒）
DEFAULT_CONNECT_TIMEOUT = 20.0
#: 工具调用的默认超时（秒）
DEFAULT_CALL_TIMEOUT = 60.0
#: 关闭时等待任务收尾的超时（秒）
DEFAULT_CLOSE_TIMEOUT = 10.0
#: Function Calling 工具名长度上限（OpenAI 兼容约定）
MAX_TOOL_NAME = 64

#: 合法工具名字符（OpenAI 兼容：字母数字与 _ -）
_INVALID_NAME_CHARS = re.compile(r"[^0-9A-Za-z_-]+")

#: 不应被业务逻辑吞掉的进程级异常
_FATAL = (KeyboardInterrupt, SystemExit)


@dataclass
class MCPServerStatus:
    """单个 MCP Server 的连接状态（前端展示 + 错误提示用）。"""

    name: str
    ok: bool = False
    transport: str = ""
    target: str = ""
    #: 暴露给 LLM 的工具名（已按需加前缀）
    tools: List[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "type": self.transport,
            "target": self.target,
            "tools": list(self.tools),
            "tool_count": len(self.tools),
            "error": self.error,
        }

    def summary(self) -> str:
        if self.ok:
            return f"{self.name}: 已连接（{len(self.tools)} 个工具，{self.transport}）"
        return f"{self.name}: 连接失败 -> {self.error}"


@dataclass
class _Actor:
    """一个 Server 的常驻任务（连接 + 收发消息 + 收尾）。"""

    config: MCPServerConfig
    task: "asyncio.Task"
    queue: "asyncio.Queue"
    #: 连接成功后回填（诊断用：读取关闭阶段的根因错误）
    connection: Optional[MCPServerConnection] = None


def sanitize_tool_name(raw: str) -> str:
    """把任意字符串规范成 Function Calling 可用的工具名片段。"""
    cleaned = _INVALID_NAME_CHARS.sub("_", str(raw or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("_-")
    return cleaned or "tool"


class MCPManager:
    """MCP 客户端生命周期 + 工具映射 + 结果转发。"""

    def __init__(
        self,
        config_path: Path | str,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> None:
        self._config_path = Path(config_path)
        self._connect_timeout = float(connect_timeout)
        self._call_timeout = float(call_timeout)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        #: server 名 -> 常驻任务
        self._actors: Dict[str, _Actor] = {}
        #: 暴露名 -> (server 名, server 上的原始工具名)
        self._tool_map: Dict[str, Tuple[str, str]] = {}
        self._timeouts: Dict[str, float] = {}
        self._statuses: List[MCPServerStatus] = []
        self._closed = False
        self._lock = threading.Lock()
        atexit.register(self.close)

    # -- 只读状态 -----------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def statuses(self) -> List[MCPServerStatus]:
        """全部 Server 的连接状态（含配置解析失败记录）。"""
        return list(self._statuses)

    @property
    def tools(self) -> Dict[str, Tuple[str, str]]:
        """LLM 工具名 -> (server, 原始工具名) 映射快照。"""
        return dict(self._tool_map)

    def describe(self) -> List[dict]:
        return [status.as_dict() for status in self._statuses]

    # -- 连接 ---------------------------------------------------------------
    def connect(self, registry: ToolRegistry) -> List[MCPServerStatus]:
        """读取 ``.mcp.json``，连接全部 Server 并把工具注册进 ``registry``。

        任何单点失败（配置非法 / 网络不通 / SDK 缺失）都只产生一条
        ``ok=False`` 的状态记录，不影响本地工具与其它 Server。
        """
        with self._lock:
            self._closed = False

        servers, error = load_mcp_config(self._config_path)
        if error:
            self._statuses.append(
                MCPServerStatus(
                    name=self._config_path.name,
                    ok=False,
                    error=error,
                )
            )
        if not servers:
            return self.statuses

        import_error = _check_sdk()
        if import_error:
            for config in servers.values():
                self._statuses.append(
                    MCPServerStatus(
                        name=config.name,
                        ok=False,
                        transport=config.transport,
                        target=config.target,
                        error=import_error,
                    )
                )
            return self.statuses

        results = self._connect_all(servers)
        for config in servers.values():
            self._append_connected(config, results.get(config.name), registry)
        return self.statuses

    def _connect_all(
        self, servers: Dict[str, MCPServerConfig]
    ) -> Dict[str, object]:
        """在事件循环线程里并发连接全部 Server，返回 ``{名: 工具表或异常}``"""
        try:
            loop = self._ensure_loop()
        except RuntimeError as exc:  # pragma: no cover - 极端环境失败
            return {name: MCPTransportError(str(exc)) for name in servers}

        future = asyncio.run_coroutine_threadsafe(
            self._start_actors(servers), loop
        )
        # 并发连接：整体预算 = 单 server 超时 + 余量
        total_timeout = self._connect_timeout + 15
        try:
            return future.result(timeout=total_timeout)
        except FutureTimeoutError:
            future.cancel()
            return {
                name: MCPTransportError(
                    f"连接超时（超过 {self._connect_timeout:.0f} 秒无响应）"
                )
                for name in servers
            }
        except _FATAL:  # pragma: no cover - 透传进程级异常
            raise
        except BaseException as exc:  # noqa: BLE001 - 线程/事件循环层异常
            return {name: _describe_error(exc) for name in servers}

    async def _start_actors(
        self, servers: Dict[str, MCPServerConfig]
    ) -> Dict[str, object]:
        """为每个 server 启动常驻任务并等待就绪，失败只写进返回值。"""

        async def start(config: MCPServerConfig) -> Tuple[str, object]:
            queue: asyncio.Queue = asyncio.Queue()
            ready: asyncio.Future = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(
                self._actor(config, queue, ready),
                name=f"zagent-mcp-{config.name}",
            )
            self._actors[config.name] = _Actor(config=config, task=task, queue=queue)
            try:
                outcome = await asyncio.wait_for(
                    asyncio.shield(ready), timeout=self._connect_timeout
                )
            except asyncio.TimeoutError:
                outcome = MCPTransportError(
                    f"连接超时（超过 {self._connect_timeout:.0f} 秒无响应）"
                )
                task.cancel()
            except _FATAL:  # pragma: no cover
                task.cancel()
                raise
            except BaseException as exc:  # noqa: BLE001
                outcome = exc
            if isinstance(outcome, BaseException) and not isinstance(outcome, _FATAL):
                self._actors.pop(config.name, None)
            return config.name, outcome

        pairs = await asyncio.gather(
            *(start(config) for config in servers.values()),
            return_exceptions=False,
        )
        return dict(pairs)

    async def _actor(
        self,
        config: MCPServerConfig,
        queue: "asyncio.Queue",
        ready: "asyncio.Future",
    ) -> None:
        """单个 Server 的常驻任务：连接 -> 循环处理调用请求 -> 同任务内关闭。"""
        connection = MCPServerConnection(config)
        pending: List["asyncio.Future"] = []
        try:
            try:
                await connection.connect()
            except _FATAL:  # pragma: no cover
                raise
            except BaseException as exc:  # noqa: BLE001 - 含 SDK 的异常组
                # 先关传输层：SDK 往往在关闭阶段才抛出真正的根因（连接被拒绝等）
                await connection.close()
                if not ready.done():
                    ready.set_result(
                        _combine_error(_describe_error(exc), connection.close_error)
                    )
                return

            self._actors_ready(config.name, connection)
            if not ready.done():
                ready.set_result(list(connection.tools))

            while True:
                message = await queue.get()
                kind = message[0]
                if kind == "close":
                    break
                if kind != "call":  # pragma: no cover - 防御性分支
                    continue
                _, remote, arguments, reply = message
                if reply.done():
                    continue
                pending.append(reply)
                try:
                    result = await connection.call_tool(remote, arguments)
                except _FATAL:  # pragma: no cover
                    raise
                except BaseException as exc:  # noqa: BLE001
                    if not reply.done():
                        reply.set_exception(exc)
                else:
                    if not reply.done():
                        reply.set_result(result)
                finally:
                    if reply in pending:
                        pending.remove(reply)
        except _FATAL:  # pragma: no cover - 进程级异常直接上抛
            raise
        except BaseException as exc:  # noqa: BLE001 - 连接中断等
            if not ready.done():
                ready.set_result(_describe_error(exc))
            error = _as_transport_error(exc)
            for reply in pending:
                if not reply.done():
                    reply.set_exception(error)
        finally:
            for reply in pending:
                if not reply.done():
                    reply.set_exception(
                        MCPTransportError(f"MCP 服务器 {config.name} 连接已关闭")
                    )
            await connection.close()
            self._actors.pop(config.name, None)

    def _actors_ready(self, name: str, connection: MCPServerConnection) -> None:
        """登记超时参数与连接句柄（由 actor 任务线程回填）。"""
        self._timeouts[name] = connection.config.timeout or self._call_timeout
        actor = self._actors.get(name)
        if actor is not None:
            actor.connection = connection

    def _append_connected(
        self,
        config: MCPServerConfig,
        outcome: object,
        registry: ToolRegistry,
    ) -> None:
        status = MCPServerStatus(
            name=config.name,
            transport=config.transport,
            target=config.target,
        )
        if isinstance(outcome, BaseException):
            status.error = _describe_error(outcome)
            self._statuses.append(status)
            return
        if isinstance(outcome, str):  # actor 传回的错误文本
            status.error = outcome
            self._statuses.append(status)
            return
        if not isinstance(outcome, list):  # pragma: no cover - 防御性分支
            status.error = f"未知的连接结果 -> {outcome!r}"
            self._statuses.append(status)
            return

        status.ok = True
        for tool in outcome:
            exposed = self._register_tool(registry, config.name, tool)
            if exposed:
                status.tools.append(exposed)
        if not outcome:
            status.error = "连接成功，但 Server 未提供任何工具"
        self._statuses.append(status)

    # -- 工具注册与命名 -----------------------------------------------------
    def _register_tool(
        self, registry: ToolRegistry, server: str, tool: MCPToolInfo
    ) -> str:
        """把 MCP 工具包装成 ToolSpec 注册；返回暴露给 LLM 的工具名。"""
        exposed = self._pick_name(registry, server, tool.name)
        handler = self._make_handler(server, tool.name, exposed)
        description = tool.description or (
            f"MCP 服务器 {server} 提供的工具 {tool.name}。"
        )
        description = f"{description}（来自 MCP 服务器 {server}）"
        try:
            registry.register(
                ToolSpec(
                    name=exposed,
                    description=description,
                    parameters=tool.input_schema
                    or {"type": "object", "properties": {}},
                    handler=handler,
                )
            )
        except Exception:  # noqa: BLE001 - 注册失败不影响其它工具
            return ""
        self._tool_map[exposed] = (server, tool.name)
        return exposed

    def _pick_name(self, registry: ToolRegistry, server: str, tool: str) -> str:
        """冲突时依次尝试：原名 -> ``服务器_工具名`` -> 追加序号。"""
        base = sanitize_tool_name(tool)
        prefixed = sanitize_tool_name(f"{server}_{tool}")
        candidates = [base, prefixed]
        for index in range(2, 20):
            candidates.append(f"{prefixed}_{index}")
        for candidate in candidates:
            candidate = candidate[:MAX_TOOL_NAME]
            if candidate and registry.get(candidate) is None:
                return candidate
        return f"mcp_{abs(hash((server, tool))) % 10 ** 8}"[:MAX_TOOL_NAME]

    def _make_handler(self, server: str, remote: str, exposed: str):
        """构造与 ``ToolSpec.handler`` 签名兼容的同步处理函数。"""

        def handler(**kwargs) -> str:
            return self.call(server, remote, kwargs, exposed=exposed)

        handler.__name__ = f"mcp_{sanitize_tool_name(exposed)}"
        handler.__doc__ = f"MCP 转发：{server}.{remote}"
        return handler

    # -- 工具调用 -----------------------------------------------------------
    def call(
        self,
        server: str,
        remote: str,
        arguments: Optional[dict] = None,
        *,
        exposed: str = "",
    ) -> str:
        """把一次 Function Call 转发给对应 Server，返回回传给模型的文本。"""
        label = exposed or f"{server}.{remote}"
        actor = self._actors.get(server)
        if actor is None:
            return f"错误：MCP 服务器 {server} 未连接，无法调用 {remote}"

        loop = self._loop
        if loop is None or not loop.is_running():
            return f"错误：MCP 客户端事件循环未运行（服务器 {server} 不可用）"

        timeout = self._timeouts.get(server) or self._call_timeout
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._invoke(actor, remote, arguments), loop
            )
        except RuntimeError as exc:  # pragma: no cover - 循环已关闭
            return f"错误：MCP 服务器 {server} 已关闭 -> {exc}"

        try:
            result = future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            return (
                f"错误：调用 MCP 工具 {label} 超时（超过 {timeout:.0f} 秒），"
                "请稍后重试或检查服务器状态"
            )
        except _FATAL:  # pragma: no cover
            raise
        except Exception as exc:  # noqa: BLE001 - 协议错误转成错误文本
            connection = actor.connection
            root = connection.close_error if connection is not None else ""
            return (
                f"错误：MCP 服务器 {server} 调用 {remote} 失败 → "
                f"{_combine_error(_describe_error(exc), root)}"
            )
        return format_tool_result(result, label=label)

    async def _invoke(
        self,
        actor: _Actor,
        remote: str,
        arguments: Optional[dict],
    ) -> object:
        """把调用请求投递给 actor 任务并等待结果。"""
        if actor.task.done():
            if actor.task.cancelled():
                raise MCPTransportError("连接任务已取消")
            error = actor.task.exception()
            raise _as_transport_error(
                error or MCPTransportError("连接已关闭")
            )
        reply: asyncio.Future = asyncio.get_running_loop().create_future()
        await actor.queue.put(("call", remote, dict(arguments or {}), reply))
        return await reply

    # -- 事件循环线程 -------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and self._loop.is_running():
            return self._loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever,
            name="zagent-mcp-loop",
            daemon=True,
        )
        thread.start()
        self._loop, self._thread = loop, thread
        return loop

    # -- 关闭 ---------------------------------------------------------------
    async def _shutdown_actors(self) -> None:
        actors = list(self._actors.values())
        self._actors.clear()
        self._timeouts.clear()
        self._tool_map.clear()
        for actor in actors:
            if not actor.task.done():
                try:
                    await asyncio.wait_for(actor.queue.put(("close", None)), 1)
                except Exception:  # noqa: BLE001 - 队列满/任务已退
                    actor.task.cancel()
        if not actors:
            return
        done, still = await asyncio.wait(
            [actor.task for actor in actors],
            timeout=DEFAULT_CLOSE_TIMEOUT,
        )
        for task in still:  # pragma: no cover - 收尾超时兜底
            task.cancel()
        # 取回任务异常，避免 "Task exception was never retrieved" 噪声
        for task in done:
            if not task.cancelled():
                task.exception()

    def close(self) -> None:
        """关闭全部连接并停掉事件循环线程（幂等，可被 atexit 调用）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._shutdown_actors(), loop
                )
                future.result(timeout=DEFAULT_CLOSE_TIMEOUT + 5)
            except _FATAL:  # pragma: no cover
                raise
            except Exception:  # noqa: BLE001 - 关闭阶段不抛异常
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:  # pragma: no cover - 循环已停
                pass
            if thread is not None:
                thread.join(timeout=5)
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass
        else:
            self._actors.clear()
        self._loop = None
        self._thread = None


# -- 模块级辅助 -------------------------------------------------------------
def _check_sdk() -> str:
    """检查 mcp SDK 是否可用，返回错误文本或空串。"""
    import importlib.util

    try:
        available = importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):  # pragma: no cover - 环境异常
        available = False
    if not available:
        return (
            "未安装 mcp SDK，无法连接 MCP Server；"
            "请执行：pip install mcp（或 pip install -r requirements.txt）"
        )
    return ""


def _exception_leaves(exc: BaseException) -> List[BaseException]:
    """展开异常组（ExceptionGroup）拿到叶子异常。"""
    leaves: List[BaseException] = []
    exceptions = getattr(exc, "exceptions", None)
    if isinstance(exceptions, (list, tuple)):
        for child in exceptions:
            leaves.extend(_exception_leaves(child))
        return leaves
    return [exc]


def _describe_error(exc: BaseException) -> str:
    """把 SDK 异常整理成前端可读的中文错误（异常组会被展开）。"""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "连接超时（服务器无响应或网络不可达）"
    if isinstance(exc, MCPTransportError):
        return str(exc)

    leaves = _exception_leaves(exc)
    # 优先给出最具体的原因，跳过取消 / 关闭这类噪音
    useful = [
        leaf
        for leaf in leaves
        if not isinstance(
            leaf,
            (asyncio.CancelledError, GeneratorExit, TimeoutError),
        )
    ]
    chosen = useful or leaves
    parts = []
    for leaf in chosen[:3]:
        text = str(leaf).strip()
        head = f"{type(leaf).__name__}: " if not text else ""
        parts.append(f"{head}{text or type(leaf).__name__}")
    return "；".join(parts) or f"{type(exc).__name__}: {exc}"


def _as_transport_error(exc: BaseException) -> MCPTransportError:
    """把任意异常包成 :class:`MCPTransportError`（保留可读信息）。"""
    if isinstance(exc, MCPTransportError):
        return exc
    return MCPTransportError(_describe_error(exc))


#: 典型的“取消噪音”关键词（SDK 内部取消作用域，不是真正的失败原因）
_NOISE_TOKENS = ("cancel scope", "cancelled", "canceled", "generatorexit")


def _is_noise(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in _NOISE_TOKENS)


def _combine_error(detail: str, root: str) -> str:
    """把表面错误与关闭阶段拿到的根因合并成一条可诊断的信息。"""
    if not root or root == detail:
        return detail
    if _is_noise(detail):
        if _is_noise(root):  # 两边都是噪音：保留表面信息
            return detail
        return f"{root}；握手被中断（{detail}）"
    if _is_noise(root):
        return detail
    return f"{detail}；{root}"


__all__ = [
    "DEFAULT_CALL_TIMEOUT",
    "DEFAULT_CONNECT_TIMEOUT",
    "MCPManager",
    "MCPServerStatus",
    "sanitize_tool_name",
]
