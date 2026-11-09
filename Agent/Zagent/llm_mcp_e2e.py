# -*- coding: utf-8 -*-
"""端到端验证：让真实 LLM 通过 Function Calling 调用 MCP 工具。

用法::

    python llm_mcp_e2e.py ["你的提问"]

默认提问会促使模型调用 Bazi-MCP 的 getBaziDetail 工具；
脚本打印事件流（工具调用 -> 结果 -> 最终回答），用于确认 MCP 工具
与本地工具在 Agent 流程中行为一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import AgentService, load_settings  # noqa: E402
from core.events import (  # noqa: E402
    AssistantText,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnError,
)

QUESTION = (
    "请调用八字工具，排出 1990年3月1日 13:00 出生男性的八字，"
    "并简要说明日主强弱。必须使用工具获取数据。"
)


def main() -> int:
    question = " ".join(sys.argv[1:]).strip() or QUESTION
    service = AgentService(load_settings())
    info = service.describe()
    print("工具数:", len(info["tools"]))
    print("MCP:", info["mcp"])

    session = service.new_session()
    try:
        used_tools = []
        for event in session.ask(question):
            if isinstance(event, ToolCallStart):
                used_tools.append(event.name)
                print(f"\n[调用] {event.name}({event.arguments})")
            elif isinstance(event, ToolCallEnd):
                flag = "ok" if event.ok else "ERR"
                print(f"[结果-{flag}] {(event.result or '')[:400]}")
            elif isinstance(event, AssistantText):
                print(f"\n[助手]\n{event.text}")
            elif isinstance(event, TurnError):
                print(f"[错误] {event.category}: {event.message}", file=sys.stderr)
            elif isinstance(event, TurnEnd):
                print(f"\n[结束] ok={event.ok} 消息数={event.message_count}")

        mcp_tool_names = {
            name
            for row in info.get("mcp") or []
            for name in row.get("tools", [])
        }
        mcp_tools = [name for name in used_tools if name in mcp_tool_names]
        print("\n本轮调用工具:", used_tools)
        print("其中 MCP 工具:", mcp_tools)
        return 0 if used_tools else 1
    finally:
        service.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
