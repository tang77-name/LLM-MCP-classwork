# -*- coding: utf-8 -*-
"""Codex 风格 Web 前端的本地 HTTP 服务。

设计要点：

- 仅依赖标准库（``http.server`` / ``json`` / ``threading``），无需 Node 构建；
- 浏览器通过轮询 ``/api/sessions/<id>/events`` 获取一次问答的事件流，
  服务端把 core 的 :class:`~core.events.Event` 序列化为带序号的 JSON；
- 每个会话同一时刻只允许一个生成任务，支持取消；
- 会话历史落盘在 ``.zagent/sessions/``，刷新页面 / 重启后仍在。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from core.agent import AgentService, AgentSession
from core.events import Event

#: 每个会话保留的最新事件数量（轮询游标过旧时前端会做一次全量刷新）
EVENT_BUFFER_LIMIT = 800

_CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


class SessionBusy(RuntimeError):
    """同一会话已有生成任务在运行。"""


def _event_payload(event: Event) -> Dict[str, Any]:
    payload = dataclasses.asdict(event)
    payload["type"] = type(event).__name__
    return payload


class CodexWebApp:
    """Web 界面的应用状态（会话缓存、事件缓冲区、运行中的任务）。"""

    def __init__(self, service: AgentService) -> None:
        self.service = service
        self.store = service.sessions
        self._lock = threading.RLock()
        self._sessions: Dict[str, AgentSession] = {}
        self._running: Dict[str, threading.Event] = {}
        self._buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._seq: Dict[str, int] = {}

    # -- 会话对象 -----------------------------------------------------------
    def session_for(self, session_id: str) -> Optional[AgentSession]:
        """取得（或按需加载）会话对象；记录不存在时返回 None。"""
        with self._lock:
            cached = self._sessions.get(session_id)
            if cached is not None:
                return cached
        session = self.service.open_session(session_id)
        if session is None:
            return None
        with self._lock:
            return self._sessions.setdefault(session_id, session)

    def drop_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    # -- 事件缓冲 -----------------------------------------------------------
    def emit(self, session_id: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            seq = self._seq.get(session_id, 0) + 1
            self._seq[session_id] = seq
            item = {**payload, "seq": seq}
            buffer = self._buffers.setdefault(session_id, [])
            buffer.append(item)
            if len(buffer) > EVENT_BUFFER_LIMIT:
                del buffer[: len(buffer) - EVENT_BUFFER_LIMIT]

    def events_since(
        self, session_id: str, since: int
    ) -> Tuple[int, List[Dict[str, Any]]]:
        with self._lock:
            seq = self._seq.get(session_id, 0)
            events = [
                event
                for event in self._buffers.get(session_id, [])
                if event["seq"] > since
            ]
            return seq, events

    def is_running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._running

    # -- 生成任务 -----------------------------------------------------------
    def ask(self, session_id: str, content: str) -> None:
        text = (content or "").strip()
        if not text:
            raise ValueError("内容不能为空")
        if self.store.get(session_id) is None:
            raise KeyError("会话不存在")
        #: /compact 走压缩流程（归档 -> LLM 摘要 -> 替换），而非普通提问
        is_compact = text.lower() == "/compact"
        with self._lock:
            if session_id in self._running:
                raise SessionBusy("该会话正在生成回复")
            cancel = threading.Event()
            self._running[session_id] = cancel
        thread = threading.Thread(
            target=self._ask_worker,
            args=(session_id, text, cancel, is_compact),
            name=f"zagent-web-{session_id}",
            daemon=True,
        )
        thread.start()

    def _ask_worker(
        self,
        session_id: str,
        content: str,
        cancel: threading.Event,
        is_compact: bool = False,
    ) -> None:
        session = self.session_for(session_id)
        if session is None:
            self.emit(
                session_id,
                {
                    "type": "TurnError",
                    "category": "internal",
                    "message": "会话不存在或已被删除",
                },
            )
            self._finish(session_id)
            return

        if not is_compact:
            self.emit(session_id, {"type": "UserMessage", "content": content})
        cancelled = False
        try:
            events = session.compact() if is_compact else session.ask(content)
            for event in events:
                if cancel.is_set():
                    cancelled = True
                    break
                self.emit(session_id, _event_payload(event))
        except Exception as exc:  # noqa: BLE001 - 任何异常都转成事件
            self.emit(
                session_id,
                {
                    "type": "TurnError",
                    "category": "internal",
                    "message": str(exc),
                },
            )
        finally:
            if cancelled:
                self.emit(session_id, {"type": "Cancelled"})
            try:
                session.persist()
            except Exception:  # noqa: BLE001
                pass
            self._finish(session_id)

    def _finish(self, session_id: str) -> None:
        with self._lock:
            self._running.pop(session_id, None)
        self.emit(session_id, {"type": "Idle"})

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            cancel = self._running.get(session_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    # -- 查询 ---------------------------------------------------------------
    def list_sessions(self, *, include_archived: bool = False) -> List[dict]:
        records = self.service.list_sessions(
            include_archived=include_archived
        )
        return [record.to_summary() for record in records]

    def state(self) -> Dict[str, Any]:
        settings = self.service.settings
        with self._lock:
            running = sorted(self._running)
        return {
            "model": settings.model_name,
            "base_url": settings.base_url,
            "workspace": str(self.service.project_root),
            "skills_dir": str(self.service.skills.skills_dir),
            "market_dir": str(self.service.market.market_dir),
            "tools": self.service.registry.names(),
            "mcp": self.service.describe().get("mcp", []),
            "skills": [
                {"name": skill.name, "description": skill.description}
                for skill in self.service.skills.skills
            ],
            "running": running,
            "web": {
                "host": settings.web_host,
                "port": settings.web_port,
            },
            "session_count": self.store.count(),
        }

    def search(self, query: str, *, include_archived: bool = False) -> List[dict]:
        return self.store.search(
            query,
            workspace=str(self.service.project_root),
            include_archived=include_archived,
        )

    def create_session(self, title: str = "") -> dict:
        record = self.service.create_session(title=title)
        return record.to_dict()

    def read_session(self, session_id: str) -> Optional[dict]:
        record = self.store.get(session_id)
        if record is None:
            return None
        if record.unread:
            self.store.set_unread(session_id, False)
            record.unread = False
        return record.to_dict()

    def update_session(self, session_id: str, fields: Dict[str, Any]) -> Optional[dict]:
        record = self.store.get(session_id)
        if record is None:
            return None
        if "title" in fields:
            record = self.store.rename(session_id, str(fields.get("title") or ""))
        if "pinned" in fields:
            record = self.store.set_pinned(session_id, bool(fields["pinned"]))
        if "archived" in fields:
            record = self.store.set_archived(session_id, bool(fields["archived"]))
        if "unread" in fields:
            record = self.store.set_unread(session_id, bool(fields["unread"]))
        return record.to_dict() if record else None

    def fork_session(self, session_id: str) -> Optional[dict]:
        record = self.store.fork(session_id)
        return record.to_dict() if record else None

    def clear_session(self, session_id: str) -> bool:
        if self.is_running(session_id):
            raise SessionBusy("生成过程中不能清空会话")
        session = self.session_for(session_id)
        if session is None:
            return False
        event = session.clear()
        self.emit(session_id, _event_payload(event))
        return True

    def delete_session(self, session_id: str) -> bool:
        self.cancel(session_id)
        deleted = self.store.delete(session_id)
        self.drop_session(session_id)
        with self._lock:
            self._buffers.pop(session_id, None)
            self._seq.pop(session_id, None)
        return deleted


def make_handler(app: CodexWebApp, page: str):
    """构造绑定到 :class:`CodexWebApp` 的请求处理器类。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ZagentCodexWeb/1.0"

        # -- 基础输出 ---------------------------------------------------
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # 静默访问日志，避免终端被轮询刷屏

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(body, _CONTENT_TYPES["json"], status)

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _read_json(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > 4 * 1024 * 1024:
                raise ValueError("请求体过大")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("请求体不是合法 JSON") from exc
            return data if isinstance(data, dict) else {}

        # -- 路由辅助 ---------------------------------------------------
        @staticmethod
        def _session_path(path: str) -> Tuple[Optional[str], str]:
            """把 ``/api/sessions/<id>/<action>`` 拆成 (id, action)。"""
            if not path.startswith("/api/sessions/"):
                return None, ""
            rest = path[len("/api/sessions/") :].strip("/")
            if not rest:
                return "", ""
            parts = rest.split("/", 1)
            return parts[0], parts[1] if len(parts) > 1 else ""

        # -- HTTP 方法 --------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path in ("/", "/index.html"):
                    self._send(page.encode("utf-8"), _CONTENT_TYPES["html"])
                    return
                if path == "/favicon.ico":
                    self._send(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
                    return
                if path == "/api/state":
                    self._json(app.state())
                    return
                if path == "/api/search":
                    text = (query.get("q") or [""])[0]
                    include_archived = (
                        (query.get("archived") or [""])[0] == "1"
                    )
                    self._json(
                        {
                            "query": text,
                            "results": app.search(
                                text, include_archived=include_archived
                            ),
                        }
                    )
                    return
                if path == "/api/sessions":
                    include_archived = (
                        (query.get("archived") or [""])[0] == "1"
                    )
                    self._json(
                        {
                            "sessions": app.list_sessions(
                                include_archived=include_archived
                            )
                        }
                    )
                    return
                session_id, action = self._session_path(path)
                if session_id:
                    if action == "":
                        record = app.read_session(session_id)
                        if record is None:
                            self._error(HTTPStatus.NOT_FOUND, "会话不存在")
                            return
                        self._json(record)
                        return
                    if action == "events":
                        since_raw = (query.get("since") or ["0"])[0]
                        try:
                            since = max(0, int(since_raw))
                        except ValueError:
                            since = 0
                        seq, events = app.events_since(session_id, since)
                        self._json(
                            {
                                "seq": seq,
                                "events": events,
                                "running": app.is_running(session_id),
                            }
                        )
                        return
                self._error(HTTPStatus.NOT_FOUND, "未知接口")
            except Exception as exc:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json()
                if path == "/api/sessions":
                    session = app.create_session(
                        str(body.get("title") or "")
                    )
                    self._json(session, HTTPStatus.CREATED)
                    return
                session_id, action = self._session_path(path)
                if not session_id:
                    self._error(HTTPStatus.NOT_FOUND, "未知接口")
                    return
                if action == "ask":
                    app.ask(session_id, str(body.get("content") or ""))
                    self._json({"started": True}, HTTPStatus.ACCEPTED)
                    return
                if action == "cancel":
                    cancelled = app.cancel(session_id)
                    self._json({"cancelled": cancelled})
                    return
                if action == "clear":
                    cleared = app.clear_session(session_id)
                    self._json({"cleared": cleared})
                    return
                if action == "fork":
                    session = app.fork_session(session_id)
                    if session is None:
                        self._error(HTTPStatus.NOT_FOUND, "会话不存在")
                        return
                    self._json(session, HTTPStatus.CREATED)
                    return
                self._error(HTTPStatus.NOT_FOUND, "未知接口")
            except SessionBusy as exc:
                self._error(HTTPStatus.CONFLICT, str(exc))
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc).strip("'"))
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_PATCH(self) -> None:  # noqa: N802
            session_id, action = self._session_path(urlparse(self.path).path)
            if not session_id or action:
                self._error(HTTPStatus.NOT_FOUND, "未知接口")
                return
            try:
                session = app.update_session(session_id, self._read_json())
                if session is None:
                    self._error(HTTPStatus.NOT_FOUND, "会话不存在")
                    return
                self._json(session)
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_DELETE(self) -> None:  # noqa: N802
            session_id, action = self._session_path(urlparse(self.path).path)
            if not session_id or action:
                self._error(HTTPStatus.NOT_FOUND, "未知接口")
                return
            try:
                deleted = app.delete_session(session_id)
                if not deleted:
                    self._error(HTTPStatus.NOT_FOUND, "会话不存在")
                    return
                self._json({"deleted": True})
            except Exception as exc:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    return Handler


class CodexWebServer:
    """线程化的本地 HTTP 服务（便于测试与嵌入）。"""

    def __init__(
        self,
        service: AgentService,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        page: str,
    ) -> None:
        self.app = CodexWebApp(service)
        self.host = host if host is not None else service.settings.web_host
        self.port = port if port is not None else service.settings.web_port
        self._page = page
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "CodexWebServer":
        if self._httpd is not None:
            return self
        handler = make_handler(self.app, self._page)

        class QuietHTTPServer(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):  # noqa: ANN001
                error = sys.exc_info()[1]
                if isinstance(
                    error,
                    (ConnectionResetError, ConnectionAbortedError, BrokenPipeError),
                ):
                    return  # 浏览器关闭连接 / 刷新页面属于正常现象
                super().handle_error(request, client_address)

        self._httpd = QuietHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="zagent-web-server",
            daemon=True,
        )
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        host, port = self.server_address
        shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        return f"http://{shown}:{port}/"

    @property
    def server_address(self) -> Tuple[str, int]:
        if self._httpd is None:
            raise RuntimeError("服务尚未启动")
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=3)


__all__ = ["CodexWebApp", "CodexWebServer", "SessionBusy", "make_handler"]
