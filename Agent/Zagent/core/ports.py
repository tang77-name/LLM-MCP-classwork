# -*- coding: utf-8 -*-
"""前端端口（Port）—— 六边形架构中 core 与界面之间的契约。

core 定义“界面能做什么”，具体界面（``cli`` / ``tui`` / ``web`` / 未来的
``desktop`` / ``mobile``）作为适配器（Adapter）实现本接口：

1. 通过 :class:`~core.agent.service.AgentService` 获取配置与会话；
2. 消费 :meth:`~core.agent.session.AgentSession.ask` 产出的事件流；
3. 把事件渲染成自己的表现形式。

约定：

- 具体界面只依赖 core，界面之间互不 import（Web 版不依赖 TUI 版）；
- ``exit`` / ``clear`` 等对话命令属于交互语义，由界面解释后调用
  core 暴露的 API（``session.clear()`` / 结束自身运行循环）；
- 新增界面 = 新增一个实现本接口的顶层包，零改动 core 与既有界面。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from .agent import AgentService


class Frontend(ABC):
    """界面端口：所有前端必须实现的最小接口。"""

    #: 唯一标识（``--ui`` 参数取值，如 "cli" / "tui" / "web"）
    name: ClassVar[str]
    #: 人类可读描述（``--list-ui`` 展示）
    description: ClassVar[str] = ""

    @abstractmethod
    def run(self, service: AgentService) -> int:
        """启动界面并阻塞运行，返回进程退出码。"""

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Frontend {self.name}>"


__all__ = ["Frontend"]
