# -*- coding: utf-8 -*-
"""上下文压缩 / 监控 / 历史检索 集成自测（不调用真实 LLM）。

覆盖需求：

1. ``/compact`` 命令语义；
2. Token 估算与 80% 阈值（警告 + 暂停输入）；
3. 归档 ``.agents/context/<编号>.jsonl`` 完整保存（角色 / 内容 / 工具调用 /
   顺序）且**先归档后压缩**；
4. 压缩 Prompt 与摘要替换（保留 System Prompt 与工具定义）；
5. 失败路径：归档失败 / LLM 失败均保留原上下文且可重试；
6. ``recallContext`` 工具的关键词检索与长度控制。

用法::

    python context_test.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.agent.session import AgentSession  # noqa: E402
from core.commands import Command, classify, command_rows  # noqa: E402
from core.config import Settings  # noqa: E402
from core.context import (  # noqa: E402
    COMPACT_PROMPT,
    ContextArchive,
    ContextMonitor,
    build_compact_messages,
    cap_summary,
    estimate_tokens,
    serialize_history,
)
from core.errors import LLMNetworkError  # noqa: E402
from core.events import (  # noqa: E402
    AssistantText,
    CompactEnd,
    CompactProgress,
    CompactStart,
    ContextUsage,
    TurnEnd,
    TurnError,
)
from core.llm.types import StreamResult  # noqa: E402
from core.tools import ToolRegistry, register_recall_tool  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# -- 假 LLM 客户端 ------------------------------------------------------------
class FakeStream:
    """可指定结果 / 异常的流（接口与 ChatStream 一致）。"""

    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error

    def __iter__(self):
        if self._error is not None:
            raise self._error
        return iter(())

    @property
    def result(self) -> StreamResult:
        if self._error is not None:
            raise self._error
        return self._result


class FakeClient:
    """按顺序吐出预设结果的假客户端；同时记录每次请求用于断言。"""

    def __init__(self, results) -> None:
        self._results = list(results)
        self.calls: list = []

    @property
    def model(self) -> str:
        return "fake-model"

    @property
    def chat_url(self) -> str:
        return "fake://chat"

    def stream(self, messages, tools=None) -> FakeStream:
        self.calls.append(
            {"messages": [dict(m) for m in messages], "tools": tools}
        )
        if not self._results:
            raise AssertionError("FakeClient 预设结果已耗尽")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            return FakeStream(error=item)
        return FakeStream(result=item)


def make_session(results, *, context_dir: Path, window: int = 100_000):
    """装配一个会话：假 LLM + 空工具表 + 指定归档目录 / 窗口大小。"""
    settings = Settings(
        base_url="http://fake",
        api_key="k",
        model_name="fake-model",
        max_tool_rounds=3,
        context_window=window,
    )
    registry = ToolRegistry()
    client = FakeClient(results)
    session = AgentSession(
        client, registry, settings, "你是测试助手。",
        context_dir=context_dir,
    )
    return session, client, registry


def run(gen) -> list:
    """消费事件生成器为列表。"""
    return [event for event in gen]


# -- 测试 ---------------------------------------------------------------------
def test_commands() -> None:
    check("/compact 可被识别", classify("/compact") is Command.COMPACT)
    check("别名 compact 可被识别", classify("compact") is Command.COMPACT)
    rows = dict(command_rows())
    check(
        "/compact 出现在命令表",
        "/compact" in rows and "压缩" in rows["/compact"],
        str(rows.get("/compact")),
    )


def test_estimate() -> None:
    check("estimate_tokens 空串为 0", estimate_tokens("") == 0)
    check("estimate_tokens 中文按字计", estimate_tokens("你好世界") == 4)
    check("estimate_tokens 英文按 4 字符计", estimate_tokens("abcdefgh") == 2)


def test_monitor_threshold(tmp: Path) -> None:
    monitor = ContextMonitor(0)
    check(
        "未知窗口默认 128k",
        monitor.window == 128 * 1024 and monitor.default_window,
    )
    check("可配置窗口", ContextMonitor(64000).window == 64000)

    small = ContextMonitor(1000)
    messages = [{"role": "user", "content": "x" * 4000}]  # 估算约 1000 token
    first = small.observe(messages)
    check("越过 80% 触发警告", first.warning and first.over_threshold,
          f"{first.percent:.2f}")
    check("越过 80% 进入 blocked", small.blocked)
    second = small.observe(messages)
    check("警告只发一次", not second.warning and second.over_threshold)
    small.reset()
    check("reset 解除 blocked", not small.blocked)
    check("snapshot 不改状态", not small.snapshot(messages).warning)


def test_usage_event(tmp: Path) -> None:
    session, client, _ = make_session(
        [StreamResult(content="你好", usage={"prompt_tokens": 500})],
        context_dir=tmp / "ctx1",
        window=100_000,
    )
    events = run(session.ask("在吗"))
    usage_events = [e for e in events if isinstance(e, ContextUsage)]
    check("响应后发出 ContextUsage", len(events) >= 1 and bool(usage_events))
    if usage_events:
        usage = usage_events[0]
        check(
            "优先采用 API 报告的 prompt_tokens",
            usage.tokens >= 500,
            f"tokens={usage.tokens}",
        )
        check("窗口来自配置", usage.window == 100_000 and not usage.default_window)
    check("AssistantText 正常发出", any(isinstance(e, AssistantText) for e in events))
    check("TurnEnd 成功", any(
        isinstance(e, TurnEnd) and e.ok for e in events
    ))
    check("普通提问携带工具 schema", client.calls[0]["tools"] == [])


def test_usage_fallback_estimate(tmp: Path) -> None:
    """API 不给 usage 时退回本地估算。"""
    session, _, _ = make_session(
        [StreamResult(content="答复", usage=None)],
        context_dir=tmp / "ctx2",
        window=0,  # 未配置窗口 -> 默认 128k
    )
    events = run(session.ask("问题"))
    usage = [e for e in events if isinstance(e, ContextUsage)][0]
    check("无 usage 时用估算且 >0", usage.tokens > 0, f"tokens={usage.tokens}")
    check("估算窗口默认 128k 并标注", usage.default_window and usage.window == 128 * 1024)


def test_blocked_input(tmp: Path) -> None:
    """达到阈值后暂停普通输入，直到压缩成功。"""
    session, client, _ = make_session(
        [StreamResult(content="ok", usage={"prompt_tokens": 950})],
        context_dir=tmp / "ctx3",
        window=1000,  # 950/1000 >= 80%
    )
    run(session.ask("触发阈值"))
    check("达到 80% 后 blocked", session.blocked)
    before = session.message_count
    events = run(session.ask("还想继续提问"))
    errors = [e for e in events if isinstance(e, TurnError)]
    check("普通提问被拒绝", len(errors) == 1 and "/compact" in errors[0].message,
          errors[0].message if errors else "无 TurnError")
    check("被拒绝时历史不追加", session.message_count == before)
    check("拒绝时返回 TurnEnd(ok=False)", any(
        isinstance(e, TurnEnd) and not e.ok for e in events
    ))


def test_compact_success(tmp: Path) -> None:
    context_dir = tmp / "ctx4"
    summary = "# 工作摘要\n\n- 已完成：A 模块\n- 关键路径：src/a.py"
    session, client, registry = make_session(
        [StreamResult(content=summary)],
        context_dir=context_dir,
        window=100_000,
    )
    # 造一段带工具调用 / 工具结果的原始历史
    session._history.extend([
        {"role": "user", "content": "帮我把 config.py 里的超时改成 30"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "file0ps",
                             "arguments": '{"action":"replace","path":"config.py"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "file0ps",
         "content": "已替换 timeout=30"},
        {"role": "assistant", "content": "改好了，timeout 已改为 30 秒。"},
    ])
    before_count = session.message_count

    events = run(session.compact())
    kinds = [type(e).__name__ for e in events]
    check("压缩请求不携带工具（禁止 Function Calling）",
          client.calls and client.calls[-1]["tools"] is None,
          str(client.calls[-1]["tools"]) if client.calls else "无调用")
    check("事件序列以 CompactStart 开始", kinds[0] == "CompactStart", str(kinds))
    check("含 CompactProgress 阶段提示",
          sum(1 for e in events if isinstance(e, CompactProgress)) >= 2, str(kinds))
    ends = [e for e in events if isinstance(e, CompactEnd)]
    check("以 CompactEnd(ok=True) 结束",
          len(ends) == 1 and ends[0].ok, ends[0].message if ends else "无")
    check("压缩未被当作普通提问", not any(isinstance(e, TurnEnd) for e in events))

    # 归档文件
    files = list(context_dir.glob("*.jsonl"))
    check("归档文件已生成", len(files) == 1, str(files))
    if files:
        lines = [json.loads(x) for x in
                 files[0].read_text(encoding="utf-8").splitlines() if x.strip()]
        check("归档含 meta 头", lines[0].get("type") == "meta"
              and lines[0].get("message_count") == before_count, str(lines[0])[:120])
        check("归档行数 = meta + 消息数", len(lines) == before_count + 1,
              f"{len(lines)} vs {before_count + 1}")
        roles = [r.get("role") for r in lines[1:]]
        check("归档保留角色与顺序", roles[0] == "system" and roles[-1] == "assistant",
              str(roles))
        check("归档保留工具调用参数", any(
            r.get("tool_calls") for r in lines[1:]
        ))
        check("归档保留工具返回结果", any(
            r.get("role") == "tool" and "timeout=30" in str(r.get("content"))
            for r in lines[1:]
        ))
        archive_id = files[0].stem
        check("归档编号形如 YYYYMMDD-HHMMSS", len(archive_id) >= 15, archive_id)

        # 上下文已替换
        history = session.history
        check("压缩后仅剩 system + 摘要", len(history) == 2,
              f"len={len(history)}")
        check("System Prompt 被保留",
              history[0]["role"] == "system" and history[0]["content"] == "你是测试助手。")
        body = history[1]["content"]
        check("摘要正文被写入", body.startswith("# 工作摘要"), body[:60])
        check("摘要末尾含归档说明", "此前上下文已于" in body and archive_id in body
              and "recallContext" in body, body[-200:])
        check("解除输入限制", not session.blocked)

        # recallContext 能找回归档原文
        register_recall_tool(registry, context_dir)
        result = registry.execute("recallContext", {"text": "timeout=30"})
        check("recallContext 可检索归档原文",
              archive_id in result and "timeout=30" in result, result[:300])
        check("压缩后工具定义仍在",
              "recallContext" in registry.names(), str(registry.names()))


def test_compact_llm_failure_retry(tmp: Path) -> None:
    context_dir = tmp / "ctx5"
    session, _, _ = make_session(
        [LLMNetworkError("接口不可达")],  # 第一次压缩：LLM 失败
        context_dir=context_dir,
    )
    session._history.append({"role": "user", "content": "重要内容：端口 8080"})
    before = list(session.history)

    events = run(session.compact())
    ends = [e for e in events if isinstance(e, CompactEnd)]
    check("LLM 失败 -> CompactEnd(ok=False)",
          len(ends) == 1 and not ends[0].ok, ends[0].message if ends else "无")
    check("失败时原上下文保持不变",
          [dict(m) for m in session.history] == [dict(m) for m in before])
    check("失败信息提示可重试", "重新执行 /compact" in ends[0].message,
          ends[0].message)
    check("失败后不处于压缩状态", not session.compacting)

    # 重新执行 /compact（第二次给正常结果）
    session._client._results.append(StreamResult(content="恢复后的摘要"))
    events2 = run(session.compact())
    ends2 = [e for e in events2 if isinstance(e, CompactEnd)]
    check("重试 /compact 成功",
          len(ends2) == 1 and ends2[0].ok, ends2[0].message if ends2 else "无")
    check("重试后历史被替换", len(session.history) == 2)


def test_compact_archive_failure(tmp: Path) -> None:
    # 归档目录指向一个“文件” -> 写入必然失败
    blocker = tmp / "ctx6-blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    session, _, _ = make_session(
        [StreamResult(content="不该被调用")],
        context_dir=blocker / "context",
    )
    session._history.append({"role": "user", "content": "数据"})
    before = list(session.history)

    events = run(session.compact())
    ends = [e for e in events if isinstance(e, CompactEnd)]
    check("归档失败 -> CompactEnd(ok=False)",
          len(ends) == 1 and not ends[0].ok, ends[0].message if ends else "无")
    check("归档失败信息可见", "归档失败" in ends[0].message, ends[0].message)
    check("归档失败时上下文不变",
          [dict(m) for m in session.history] == [dict(m) for m in before])


def test_compact_rejects_concurrent_ask(tmp: Path) -> None:
    session, _, _ = make_session(
        [StreamResult(content="摘要")],
        context_dir=tmp / "ctx7",
    )
    session._history.append({"role": "user", "content": "历史"})
    gen = session.compact()
    first = next(gen)
    check("压缩开始事件", isinstance(first, CompactStart))
    check("压缩期间 compacting=True", session.compacting)

    events = run(session.ask("压缩期间的提问"))
    errors = [e for e in events if isinstance(e, TurnError)]
    check("压缩期间拒绝普通提问",
          len(errors) == 1 and "压缩" in errors[0].message,
          errors[0].message if errors else "无 TurnError")

    gen.close()
    check("压缩结束（关闭生成器）后恢复", not session.compacting)


def test_recall_context_tool(tmp: Path) -> None:
    context_dir = tmp / "ctx8"
    archive = ContextArchive(context_dir)
    aid = archive.save([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "请把数据库连接串改成 postgres://local/db"},
        {"role": "assistant", "content": "已修改 settings.py 的 DATABASE_URL"},
    ], session_id="s1")

    registry = ToolRegistry()
    register_recall_tool(registry, context_dir)
    check("recallContext 已注册", "recallContext" in registry.names())

    hit = registry.execute("recallContext", {"text": "DATABASE_URL"})
    check("关键词命中返回归档编号", aid in hit, hit[:200])
    check("关键词命中返回消息位置", "第 2 条消息" in hit, hit[:200])

    snippet_hit = registry.execute("recallContext", {"text": "postgres"})
    check("关键词命中返回原文片段",
          "postgres://local/db" in snippet_hit, snippet_hit[:300])

    scoped = registry.execute(
        "recallContext", {"text": "数据库", "id": aid}
    )
    check("指定 id 检索成功", aid in scoped, scoped[:200])

    missing = registry.execute("recallContext", {"text": "不存在的词xyzzy"})
    check("无命中给出明确提示", "未在" in missing and "xyzzy" in missing, missing[:200])

    bad_id = registry.execute("recallContext", {"text": "任何", "id": "nope-1"})
    check("归档不存在返回错误文本", bad_id.startswith("错误："), bad_id[:200])

    empty = registry.execute("recallContext", {"text": "  "})
    check("空关键词返回错误文本", empty.startswith("错误："), empty[:200])

    # 结果长度受控：大量命中时截断
    many = [
        {"role": "user", "content": f"重复内容 {i} " + "词 " * 200}
        for i in range(30)
    ]
    archive.save(many)
    big = registry.execute("recallContext", {"text": "重复内容"})
    check("检索结果长度受控", len(big) <= 3200, f"len={len(big)}")


def test_compaction_helpers() -> None:
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "RunCli", "arguments": '{"cmd":"ls"}'}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "RunCli", "content": "a.py"},
    ]
    text = serialize_history(history)
    check("序列化保留角色顺序",
          text.index("SYSTEM") < text.index("USER")
          < text.index("ASSISTANT") < text.index("TOOL"))
    check("序列化保留工具调用参数", "RunCli" in text and '{"cmd":"ls"}' in text)
    check("序列化保留工具返回结果", "a.py" in text)

    messages = build_compact_messages(history)
    check("压缩请求首条为专用 Prompt",
          messages[0]["role"] == "system" and messages[0]["content"] == COMPACT_PROMPT)
    check("压缩请求第二条为历史", messages[1]["role"] == "user"
          and "问题" in messages[1]["content"])

    long_text = "很长的摘要" * 4000
    capped = cap_summary(long_text, max_tokens=100)
    check("超长摘要被截断并注明", len(capped) < len(long_text)
          and "截断" in capped, f"{len(capped)} vs {len(long_text)}")
    check("短摘要原样保留", cap_summary("短摘要") == "短摘要")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="zagent-ctx-test-"))
    try:
        test_commands()
        test_estimate()
        test_monitor_threshold(tmp)
        test_usage_event(tmp)
        test_usage_fallback_estimate(tmp)
        test_blocked_input(tmp)
        test_compact_success(tmp)
        test_compact_llm_failure_retry(tmp)
        test_compact_archive_failure(tmp)
        test_compact_rejects_concurrent_ask(tmp)
        test_recall_context_tool(tmp)
        test_compaction_helpers()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
