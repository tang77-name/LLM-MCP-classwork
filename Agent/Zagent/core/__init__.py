# -*- coding: utf-8 -*-
"""core 层：与界面无关的业务核心（本应用唯一的业务逻辑所在地）。

分层::

    core/
    ├── config.py      配置加载（环境变量 > config.ini > 默认值）
    ├── errors.py      统一异常体系
    ├── events.py      事件模型（界面渲染的唯一输入）
    ├── ports.py       前端端口接口（Frontend）
    ├── commands.py    会话控制命令语义与命令目录（/help /save /resume 等）
    ├── llm/           LLM 接入层（OpenAI 兼容流式客户端）
    ├── skills/        Agent Skills 发现与元信息（.agents/skills）
    ├── market/        本地技能市场（浏览 / 搜索 / 安装 / 卸载）
    ├── sessions/      会话持久化（.zagent/sessions）
    ├── tools/         工具层（Function Call 定义 / 注册 / 分发 / 实现）
    ├── mcp/           MCP 客户端（.mcp.json -> 连接 / 工具发现 / 调用转发）
    ├── context/       上下文压缩（Token 监控 / 归档 / 摘要 / 检索）
    └── agent/         编排层（会话、提示词、AGENTS.md、应用服务组合根）

依赖方向：``core -> 标准库 / requests``；core 永不 import 任何前端包。
"""

__version__ = "0.1.0"

from .config import Settings, load_settings
from .events import (
    AssistantText,
    Event,
    HistoryCleared,
    LimitReached,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnError,
)
from .agent.service import AgentService
from .agent.session import AgentSession
from .ports import Frontend

__all__ = [
    "__version__",
    "Settings",
    "load_settings",
    "AgentService",
    "AgentSession",
    "Frontend",
    "Event",
    "TextDelta",
    "AssistantText",
    "ToolCallStart",
    "ToolCallEnd",
    "LimitReached",
    "TurnEnd",
    "TurnError",
    "HistoryCleared",
]
