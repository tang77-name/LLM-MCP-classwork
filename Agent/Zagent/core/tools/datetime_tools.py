# -*- coding: utf-8 -*-
"""本地日期时间工具（getLocalDateTime）。

与时区无关的“本机当前日期/时间”查询，供模型在回答涉及日期、
星期、倒计时等问题时调用。纯业务实现：不打印、不依赖界面。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .base import ToolRegistry, ToolSpec

#: 默认输出格式
DEFAULT_FORMAT = "%Y-%m-%d %H:%M:%S"
#: 星期中文名（strftime 在 Windows 上不支持 %A 中文）
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def get_local_date_time(date_format: str = DEFAULT_FORMAT) -> str:
    """getLocalDateTime：返回本机当前日期时间。

    :param date_format: strftime 格式串，默认 ``%Y-%m-%d %H:%M:%S``；
                        Windows 上仅保证 %Y %m %d %H %M %S %f %j 等 ASCII 格式符。
    :raises ValueError: 格式串为空或平台不支持。
    """
    now = datetime.now()
    fmt = (date_format or "").strip() or DEFAULT_FORMAT
    try:
        rendered = now.strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"date_format 非法 -> {fmt!r}: {exc}") from exc

    weekday = _WEEKDAYS[now.weekday()]
    return (
        f"当前本地日期时间: {now.strftime(DEFAULT_FORMAT)}（{weekday}）\n"
        f"按请求格式输出: {rendered}\n"
        f"时间戳(秒): {int(now.timestamp())}"
    )


def build_registry() -> ToolRegistry:
    """注册日期时间工具组（可与其它工具组合注册）。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="getLocalDateTime",
        description=(
            "获取本机当前日期与时间（本地时区）。"
            "当用户询问今天日期、星期、当前时间等信息时调用；"
            "date_format 可选，例如 '%%Y-%%m-%%d' 只要日期。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "date_format": {
                    "type": "string",
                    "description": (
                        "可选。strftime 格式串，默认 '%%Y-%%m-%%d %%H:%%M:%%S'；"
                        "例：'%%Y-%%m-%%d' 仅日期。"
                    ),
                }
            },
            "required": [],
        },
        handler=get_local_date_time,
    ))
    return registry


__all__ = ["get_local_date_time", "build_registry", "DEFAULT_FORMAT"]
