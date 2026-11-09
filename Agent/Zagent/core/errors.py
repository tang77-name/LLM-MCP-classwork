# -*- coding: utf-8 -*-
"""core 层统一异常体系。

所有 core 异常都继承 :class:`ZagentError`，并携带 ``category``
（供 UI 分类展示：network / api / config / tool / internal）。
"""

from __future__ import annotations


class ZagentError(Exception):
    """core 层所有业务异常的基类。"""

    category = "internal"


class ConfigError(ZagentError):
    """配置读取 / 校验失败。"""

    category = "config"


class LLMError(ZagentError):
    """LLM 接口相关错误的基类。"""

    category = "api"


class LLMNetworkError(LLMError):
    """网络不可达、超时、连接被拒等。"""

    category = "network"


class LLMResponseError(LLMError):
    """HTTP 非 200 或返回体中携带 error 字段。"""

    category = "api"


class ToolError(ZagentError):
    """工具执行失败。"""

    category = "tool"


__all__ = [
    "ZagentError",
    "ConfigError",
    "LLMError",
    "LLMNetworkError",
    "LLMResponseError",
    "ToolError",
]
