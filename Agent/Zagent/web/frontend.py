# -*- coding: utf-8 -*-
"""Codex 风格 Web 前端适配器。

:class:`WebFrontend` 只负责启动本地 HTTP 服务并打开浏览器；所有业务能力
来自 :class:`core.agent.AgentService`，界面资源见 :mod:`web.static`。
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser

from core.agent import AgentService
from core.ports import Frontend

from web.server import CodexWebServer
from web.static import PAGE


class WebFrontend(Frontend):
    """本地 Web 界面：侧栏 / 工作区会话树 / 全局搜索 / 轮次导航。"""

    name = "web"
    description = "Codex 风格 Web 界面（侧栏、会话树、全局搜索、轮次导航）"

    def run(self, service: AgentService) -> int:
        server = CodexWebServer(service, page=PAGE)
        try:
            server.start()
        except OSError as exc:
            print(f"错误：Web 服务启动失败（端口 {server.port}）：{exc}", file=sys.stderr)
            return 1

        url = server.url
        print("Zagent Codex Web 已启动")
        print(f"  地址: {url}")
        print(f"  工作区: {service.project_root}")
        print("  按 Ctrl+C 停止服务")
        sys.stdout.flush()

        if service.settings.web_open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()

        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n正在关闭 Web 服务…")
        finally:
            server.stop()
        return 0


__all__ = ["WebFrontend"]
