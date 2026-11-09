# -*- coding: utf-8 -*-
"""原始上下文归档：``.agents/context/<编号>.jsonl`` 的写入与关键词检索。

文件格式（JSONL，一行一条，便于 grep / 逐行读取）::

    {"type":"meta","archive_id":"20261022-153000","session_id":"ab12",
     "created_at":"2026-10-22T15:30:00","message_count":42}
    {"type":"message","index":0,"role":"system","content":"..."}
    {"type":"message","index":1,"role":"user","content":"..."}
    {"type":"message","index":2,"role":"assistant","content":"...",
     "tool_calls":[...]}
    {"type":"message","index":3,"role":"tool","tool_call_id":"call_0",
     "name":"RunCli","content":"..."}

- 保留**角色、内容、工具调用参数、工具返回结果与消息顺序**（原样写入）；
- 写完会回读校验行数，确保“归档成功”后才允许继续压缩；
- 检索为纯本地全文关键词匹配（空格分词，命中越多越靠前），不依赖向量库。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ArchiveMatch:
    """一条检索命中。"""

    archive_id: str   # 归档编号（文件名去后缀）
    position: int     # 消息在原上下文中的序号（0 开始）
    line: int         # 在 jsonl 文件中的行号（1 开始）
    role: str         # 消息角色
    snippet: str      # 截断后的原始内容片段
    score: int        # 命中的关键词数
    path: str         # 归档文件绝对路径


class ContextArchive:
    """``.agents/context`` 目录的归档写入器 + 关键词检索器。"""

    def __init__(self, directory) -> None:
        self.directory = Path(directory)

    # -- 写入 ---------------------------------------------------------------
    def save(self, messages, *, session_id: str = "") -> str:
        """完整保存原始消息，返回归档编号（如 ``20261022-153000``）。

        :raises OSError: 目录 / 文件写入失败（调用方必须中止后续压缩）
        :raises ValueError: 回读校验不通过（行数与消息数不符）
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.directory / f"{stamp}.jsonl"
        suffix = 1
        while path.exists():  # 同一秒内重复压缩 -> 追加序号避免覆盖
            path = self.directory / f"{stamp}-{suffix}.jsonl"
            suffix += 1
        archive_id = path.stem
        items = list(messages)

        with path.open("w", encoding="utf-8", newline="\n") as handle:
            meta = {
                "type": "meta",
                "archive_id": archive_id,
                "session_id": session_id or "",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "message_count": len(items),
            }
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for index, message in enumerate(items):
                record: Dict[str, Any] = {"type": "message", "index": index}
                if isinstance(message, dict):
                    record.update(message)
                else:  # pragma: no cover - 防御性分支
                    record["role"] = "unknown"
                    record["content"] = str(message)
                handle.write(
                    json.dumps(record, ensure_ascii=False, default=str) + "\n"
                )

        # 回读校验：只有确认写全了，才允许继续压缩 / 替换上下文
        written = self._count_messages(path)
        if written != len(items):
            raise ValueError(
                f"归档回读校验失败：期望 {len(items)} 条，实际 {written} 条 -> {path}"
            )
        return archive_id

    @staticmethod
    def _count_messages(path: Path) -> int:
        count = 0
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") == "message":
                    count += 1
        return count

    # -- 读取 ---------------------------------------------------------------
    def list_ids(self) -> List[str]:
        """目录下全部归档编号（新编号在前）。"""
        if not self.directory.is_dir():
            return []
        ids = [p.stem for p in self.directory.glob("*.jsonl")]
        return sorted(ids, reverse=True)

    def path_of(self, archive_id: str) -> Path:
        """编号 -> 文件路径（只允许纯文件名，防路径穿越）。"""
        safe = Path(str(archive_id or "")).name.strip()
        if not safe or safe != str(archive_id or "").strip():
            raise ValueError(f"归档编号非法 -> {archive_id!r}")
        return self.directory / f"{safe}.jsonl"

    def search(
        self,
        text: str,
        *,
        archive_id: Optional[str] = None,
        limit: int = 5,
        snippet_chars: int = 260,
    ) -> List[ArchiveMatch]:
        """全文关键词检索（命中关键词数降序 -> 新归档在前 -> 位置靠前在前）。

        :param text: 关键词，空白分隔多个词（中文整句也会作为整体子串匹配）
        :param archive_id: 指定归档编号；``None`` 表示检索全部归档
        :param limit: 最多返回条数
        """
        terms = [t.lower() for t in str(text or "").split() if t.strip()]
        if not terms:
            raise ValueError("text 不能为空，请提供检索关键词")

        if archive_id:
            files = [self.path_of(archive_id)]
            if not files[0].is_file():
                raise ValueError(
                    f"归档不存在 -> {archive_id}"
                    f"（现有编号：{', '.join(self.list_ids()) or '无'}）"
                )
        else:
            files = [
                self.directory / f"{aid}.jsonl" for aid in self.list_ids()
            ]

        matches: List[ArchiveMatch] = []
        for path in files:
            archive = path.stem
            try:
                handle = path.open("r", encoding="utf-8")
            except OSError:  # pragma: no cover - 竞态：文件刚被删除
                continue
            with handle:
                for line_no, raw in enumerate(handle, start=1):
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        record = {"role": "raw", "content": raw}
                    if not isinstance(record, dict):
                        continue
                    if record.get("type") == "meta":
                        continue
                    haystack = json.dumps(record, ensure_ascii=False).lower()
                    score = sum(1 for term in terms if term in haystack)
                    if score <= 0:
                        continue
                    matches.append(
                        ArchiveMatch(
                            archive_id=archive,
                            position=int(record.get("index", line_no - 1)),
                            line=line_no,
                            role=str(record.get("role") or "?"),
                            snippet=_snippet(record, terms, snippet_chars),
                            score=score,
                            path=str(path),
                        )
                    )

        # 稳定排序组合：命中数降序 -> 新归档在前 -> 位置靠前在前
        matches.sort(key=lambda m: m.position)
        matches.sort(key=lambda m: m.archive_id, reverse=True)
        matches.sort(key=lambda m: -m.score)
        return matches[: max(1, int(limit))]


def _snippet(record: dict, terms: List[str], width: int) -> str:
    """提取消息正文片段：优先围绕第一个命中关键词截取。"""
    content = record.get("content")
    if isinstance(content, list):  # 多模态等复合内容
        content = json.dumps(content, ensure_ascii=False)
    if not content:
        content = json.dumps(
            {k: v for k, v in record.items() if k not in ("type", "index")},
            ensure_ascii=False,
            default=str,
        )
    text = str(content)
    lower = text.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    if positions:
        start = max(0, min(positions) - width // 3)
        end = min(len(text), start + width)
        piece = text[start:end]
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
        return prefix + piece + suffix
    return text[:width] + ("…" if len(text) > width else "")


__all__ = ["ArchiveMatch", "ContextArchive"]
