# -*- coding: utf-8 -*-
"""会话持久化：把对话记录保存为 ``.zagent/sessions/*.json``。

:py:class:`SessionStore` 是 Web 前端“工作区会话树”的数据来源：
每个会话一条 JSON 文件，包含标题、元信息（置顶 / 归档 / 未读）与
消息历史。存储层不依赖任何界面，CLI / TUI 未来也可复用。

线程安全：所有读写都持有可重入锁；写文件采用临时文件 + 原子替换。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 会话 id 格式（防目录穿越，仅允许安全字符）
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_message(message: Any) -> Optional[Dict[str, Any]]:
    """把一条消息整理为可 JSON 序列化的精简副本。"""
    if not isinstance(message, dict):
        return None
    role = str(message.get("role", "")).strip()
    if role not in ("user", "assistant", "tool"):
        return None
    cleaned: Dict[str, Any] = {"role": role}

    content = message.get("content")
    if content is None:
        cleaned["content"] = None
    else:
        cleaned["content"] = content if isinstance(content, str) else str(content)

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if value:
            cleaned[key] = str(value)

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        cleaned["tool_calls"] = tool_calls  # 已是纯 JSON 结构
    return cleaned


def _auto_title(messages: List[Dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = " ".join(str(message.get("content") or "").split())
            if text:
                return text[:48] + ("…" if len(text) > 48 else "")
    return "新会话"


@dataclass
class SessionRecord:
    """一条会话记录（含元信息与消息历史）。"""

    id: str
    title: str = ""
    workspace: str = ""
    created_at: str = ""
    updated_at: str = ""
    pinned: bool = False
    archived: bool = False
    unread: bool = False
    message_count: int = 0
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> Dict[str, Any]:
        """列表视图：不含消息正文。"""
        return {
            "id": self.id,
            "title": self.title or "新会话",
            "workspace": self.workspace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pinned": self.pinned,
            "archived": self.archived,
            "unread": self.unread,
            "message_count": self.message_count,
        }

    def to_dict(self, *, include_messages: bool = True) -> Dict[str, Any]:
        data = self.to_summary()
        if include_messages:
            data["messages"] = copy.deepcopy(self.messages)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionRecord":
        record = cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            workspace=str(data.get("workspace", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            pinned=bool(data.get("pinned", False)),
            archived=bool(data.get("archived", False)),
            unread=bool(data.get("unread", False)),
        )
        messages = data.get("messages")
        record.messages = [
            cleaned
            for item in (messages if isinstance(messages, list) else [])
            if (cleaned := _clean_message(item)) is not None
        ]
        record.message_count = len(record.messages)
        return record


class SessionStore:
    """``.zagent/sessions/`` 目录上的会话仓库。"""

    def __init__(self, directory: os.PathLike | str) -> None:
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 目录不可写时不阻断应用启动；后续写入会失败并被持久化调用方吞掉
            pass
        self._lock = threading.RLock()
        self._cache: Dict[str, SessionRecord] = {}
        self._load_all()

    # -- 读写 ---------------------------------------------------------------
    def _path(self, session_id: str) -> Path:
        sid = str(session_id or "").strip()
        if not _SESSION_ID_RE.match(sid):
            raise ValueError(f"非法会话 id -> {session_id!r}")
        return self.directory / f"{sid}.json"

    def _load_all(self) -> None:
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError:
            return
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = SessionRecord.from_dict(data)
                if record.id and _SESSION_ID_RE.match(record.id) and path.stem == record.id:
                    self._cache[record.id] = record
            except (OSError, ValueError):
                continue  # 损坏的文件跳过，不影响其它会话

    def _write(self, record: SessionRecord) -> None:
        path = self._path(record.id)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(temp), str(path))
        finally:
            temp.unlink(missing_ok=True)

    # -- 查询 ---------------------------------------------------------------
    def get(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            return copy.deepcopy(record) if record else None

    def list(
        self,
        workspace: Optional[str] = None,
        *,
        include_archived: bool = False,
    ) -> List[SessionRecord]:
        with self._lock:
            records = list(self._cache.values())
        if workspace:
            records = [r for r in records if r.workspace == workspace]
        if not include_archived:
            records = [r for r in records if not r.archived]
        records.sort(
            key=lambda r: (r.pinned, r.updated_at or r.created_at),
            reverse=True,
        )
        return [copy.deepcopy(record) for record in records]

    def count(self) -> int:
        with self._lock:
            return len(self._cache)

    # -- 写入 ---------------------------------------------------------------
    def create(
        self,
        *,
        workspace: str = "",
        title: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> SessionRecord:
        session_id = "s" + uuid.uuid4().hex[:12]
        now = _now_iso()
        record = SessionRecord(
            id=session_id,
            title=(title or "").strip(),
            workspace=str(workspace or ""),
            created_at=now,
            updated_at=now,
        )
        record.messages = [
            cleaned
            for item in (messages or [])
            if (cleaned := _clean_message(item)) is not None
        ]
        record.message_count = len(record.messages)
        if not record.title and record.messages:
            record.title = _auto_title(record.messages)
        with self._lock:
            self._cache[session_id] = record
            self._write(record)
            return copy.deepcopy(record)

    def save(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        title: Optional[str] = None,
        mark_unread: bool = False,
    ) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            if record is None:
                return None
            record.messages = [
                cleaned
                for item in (messages or [])
                if (cleaned := _clean_message(item)) is not None
            ]
            record.message_count = len(record.messages)
            if title is not None and str(title).strip():
                record.title = str(title).strip()
            elif not record.title:
                record.title = _auto_title(record.messages)
            if mark_unread:
                record.unread = True
            record.updated_at = _now_iso()
            self._write(record)
            return copy.deepcopy(record)

    def rename(self, session_id: str, title: str) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            if record is None:
                return None
            record.title = str(title or "").strip() or record.title
            record.updated_at = _now_iso()
            self._write(record)
            return copy.deepcopy(record)

    def set_pinned(self, session_id: str, pinned: bool) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            if record is None:
                return None
            record.pinned = bool(pinned)
            record.updated_at = _now_iso()
            self._write(record)
            return copy.deepcopy(record)

    def set_archived(
        self, session_id: str, archived: bool
    ) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            if record is None:
                return None
            record.archived = bool(archived)
            record.updated_at = _now_iso()
            self._write(record)
            return copy.deepcopy(record)

    def set_unread(
        self, session_id: str, unread: bool
    ) -> Optional[SessionRecord]:
        with self._lock:
            record = self._cache.get(str(session_id or "").strip())
            if record is None:
                return None
            record.unread = bool(unread)
            self._write(record)
            return copy.deepcopy(record)

    def delete(self, session_id: str) -> bool:
        sid = str(session_id or "").strip()
        with self._lock:
            if sid not in self._cache:
                return False
            try:
                self._path(sid).unlink(missing_ok=True)
            except (OSError, ValueError):
                return False
            del self._cache[sid]
            return True

    def fork(self, session_id: str) -> Optional[SessionRecord]:
        source = self.get(session_id)
        if source is None:
            return None
        title = f"{source.title or '新会话'} (fork)"
        return self.create(
            workspace=source.workspace,
            title=title,
            messages=source.messages,
        )

    # -- 搜索 ---------------------------------------------------------------
    def search(
        self,
        query: str,
        workspace: Optional[str] = None,
        *,
        include_archived: bool = False,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """按标题 / 正文搜索会话，返回摘要与命中片段。"""
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        results: List[Dict[str, Any]] = []
        for record in self.list(
            workspace, include_archived=include_archived
        ):
            if len(results) >= limit:
                break
            summary = record.to_summary()
            if needle in (record.title or "").casefold():
                summary["snippet"] = record.title
                results.append(summary)
                continue
            for index, message in enumerate(record.messages):
                text = str(message.get("content") or "")
                position = text.casefold().find(needle)
                if position < 0:
                    continue
                start = max(0, position - 40)
                snippet = ("…" if start else "") + text[start:start + 120]
                summary["snippet"] = " ".join(snippet.split())
                # 命中消息索引，供前端跳转到对应轮次
                summary["message_index"] = index
                results.append(summary)
                break
        return results


__all__ = ["SessionRecord", "SessionStore"]
