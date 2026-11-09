# -*- coding: utf-8 -*-
"""TUI 前端包：向应用外壳导出端口实现（Textual）。"""

from .app import TUIFrontend, ZagentApp

#: 应用外壳（frontends.py）通过该名称发现本包的端口实现
Frontend = TUIFrontend

__all__ = ["Frontend", "TUIFrontend", "ZagentApp"]
