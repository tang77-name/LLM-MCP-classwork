# -*- coding: utf-8 -*-
"""Zagent Web 前端：Codex 风格的本地 Web 界面。"""

from web.frontend import WebFrontend

#: 前端自动发现约定：顶层包导出 ``Frontend``
Frontend = WebFrontend

__all__ = ["Frontend", "WebFrontend"]
