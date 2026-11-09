# -*- coding: utf-8 -*-
"""端到端验证：真实 LLM 走完整 /compact 流程 + recallContext 检索。

步骤：

1. 用真实 LLM 产生几轮对话（制造可用的历史上下文）；
2. 执行 ``session.compact()``：归档 -> LLM 摘要 -> 替换上下文；
3. 打印事件流与压缩前后的上下文占用；
4. 用 ``recallContext`` 检索归档，验证“摘要缺细节可找回原文”。

用法::

    python compact_e2e.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import AgentService, load_settings  # noqa: E402
from core.events import (  # noqa: E402
    AssistantText,
    CompactEnd,
    CompactProgress,
    CompactStart,
    ContextUsage,
    TurnEnd,
    TurnError,
)


def main() -> int:
    service = AgentService(load_settings())
    session = service.new_session()
    print(f"归档目录: {session.context_dir}")

    try:
        # 1) 制造历史（含具体事实，供压缩与检索验证）
        questions = [
            "请记住以下项目约定，稍后我会考你：数据库文件是 data/pet.db，"
            "服务端口是 8080，超时时间要求是 30 秒。",
            "再记住一个决策：我们决定用 stdio 方式启动 MCP Server，"
            "不使用 HTTP，原因是部署更简单。请复述你记住了什么。",
        ]
        for question in questions:
            print(f"\n你> {question}")
            usage_shown = False
            for event in session.ask(question):
                if isinstance(event, AssistantText):
                    print(f"Zagent> {event.text}")
                elif isinstance(event, ContextUsage):
                    window = f"{event.window / 1024:.1f}k"
                    if event.default_window:
                        window += "（默认）"
                    print(
                        f"[上下文] {event.tokens / 1024:.1f}k/{window}"
                        f" · {event.percent * 100:.1f}%"
                    )
                    usage_shown = True
                elif isinstance(event, TurnError):
                    print(f"[错误] {event.category}: {event.message}")
                elif isinstance(event, TurnEnd):
                    print(f"[结束] ok={event.ok} 消息数={event.message_count}")
            if not usage_shown:
                usage = session.context_usage
                print(f"[上下文] {usage.summary_text}")

        # 2) 执行 /compact
        print("\n" + "=" * 60)
        print("/compact 开始")
        before = session.context_usage
        for event in session.compact():
            if isinstance(event, CompactStart):
                print("[压缩] 开始")
            elif isinstance(event, CompactProgress):
                print(f"[压缩] {event.message}")
            elif isinstance(event, CompactEnd):
                flag = "完成" if event.ok else "失败"
                print(f"[压缩] {flag}：{event.message}")
                if not event.ok:
                    return 1
        after = session.context_usage
        print(f"占用变化: {before.summary_text} -> {after.summary_text}")

        # 3) 压缩后的上下文
        history = session.history
        print(f"\n压缩后消息数: {len(history)}")
        print("System Prompt 前 40 字:", str(history[0]['content'])[:40], "…")
        summary = str(history[1]['content'])
        print(f"\n摘要（共 {len(summary)} 字）前 600 字：\n" + summary[:600] + "\n…")

        # 4) recallContext 检索归档
        print("=" * 60)
        for keyword in ("data/pet.db", "stdio"):
            result = service.registry.execute(
                "recallContext", {"text": keyword}
            )
            print(f"\n[recallContext] {keyword}\n{result[:600]}")

        return 0
    finally:
        service.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
