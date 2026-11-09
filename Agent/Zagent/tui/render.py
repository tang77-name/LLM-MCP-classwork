# -*- coding: utf-8 -*-
"""TUI 前端的展示辅助（截断、压缩单行）。"""

from __future__ import annotations

import json

_ARGS_LIMIT = 300
_RESULT_LIMIT = 800


def short_json(raw: str, limit: int = _ARGS_LIMIT) -> str:
    """把工具参数 JSON 压成单行并截断，避免刷屏。"""
    try:
        parsed = json.loads(raw) if raw else {}
        text = json.dumps(parsed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        text = raw or ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " …"


def short_text(text: str, limit: int = _RESULT_LIMIT) -> str:
    """长文本（工具结果）截断。"""
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n  …（截断）"


__all__ = ["short_json", "short_text"]
