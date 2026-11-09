# -*- coding: utf-8 -*-
"""会话管理 / 配置化参数 / AGENTS.md / MCP 异步加载 自测（不调用真实 LLM）。

覆盖本轮新增需求：

1. 斜杠命令目录与 ``/save`` ``/resume`` ``/export`` ``/mcp`` ``/config`` 解析；
2. ``[tools]`` / ``[mcp]`` 配置段与内置默认值、非法值校验；
3. 首次运行自动生成 config.ini（mock 信息）与 ``startup_notice``；
4. 工具参数确实由配置注入（fetchWeb / RunCli / file0ps / recallContext）；
5. AGENTS.md「一次注入，全程复用」（构造 / 恢复 / 压缩后保留、不重复注入）；
6. 会话保存与恢复（``save_session`` / ``open_session``）、Markdown 导出；
7. MCP 异步加载（``mcp_async=True`` + ``wait_mcp``）。

用法::

    python agent_extras_test.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import AgentService  # noqa: E402
from core.agent.agents_md import (  # noqa: E402
    MARKER,
    build_message,
    ensure_injected,
    find_index,
    load_agents_md,
)
from core.agent.session import AgentSession  # noqa: E402
from core.commands import (  # noqa: E402
    Command,
    command_rows,
    parse,
    suggest,
)
from core.config import (  # noqa: E402
    DEFAULT_CONFIG_TEXT,
    Settings,
    load_settings,
    write_default_config,
)
from core.errors import ConfigError  # noqa: E402
from core.events import CompactEnd  # noqa: E402
from core.llm.types import StreamResult  # noqa: E402
from core.sessions import history_to_markdown  # noqa: E402
from core.tools import ToolRegistry, build_default_registry  # noqa: E402

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


class _Stream:
    """与 ChatStream 接口一致的假流。"""

    def __init__(self, result: StreamResult) -> None:
        self._result = result

    def __iter__(self):
        return iter(())

    @property
    def result(self) -> StreamResult:
        return self._result


class _Client:
    """按顺序返回预设结果的假 LLM 客户端。"""

    def __init__(self, results) -> None:
        self._results = list(results)
        self.calls = []

    @property
    def model(self) -> str:
        return "fake-model"

    def stream(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return _Stream(self._results.pop(0))


def _settings(**overrides) -> Settings:
    base = dict(
        base_url="http://fake", api_key="k", model_name="fake-model",
        max_tool_rounds=3, context_window=100_000,
    )
    base.update(overrides)
    return Settings(**base)


class _chdir:
    """临时切换工作目录（测试结束后还原）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous = Path.cwd()

    def __enter__(self):
        os.chdir(self.path)
        return self.path

    def __exit__(self, *exc_info):
        os.chdir(self.previous)


# -- 1. 斜杠命令 ---------------------------------------------------------------
def test_commands() -> None:
    rows = dict(command_rows())
    for name in ("/new", "/save", "/resume", "/export", "/mcp", "/config", "/quit"):
        check(f"命令表包含 {name}", name in rows, str(list(rows)))
    check("/new 说明提到自动保存", "自动保存" in rows.get("/new", ""))
    check("/resume 说明提到面板", "面板" in rows.get("/resume", ""))

    check("parse /save", parse("/save") == (Command.SAVE, ""))
    check("/save 带标题", parse("/save 我的会话") == (Command.SAVE, "我的会话"))
    check("/export 带目录", parse("/export ./out") == (Command.EXPORT, "./out"))
    check("普通文本解析为 NONE", parse("你好 /save") == (Command.NONE, "你好 /save"))
    check("别名 /q 退出", parse("q")[0] is Command.EXIT)
    check("/mcp 可识别", parse("/mcp")[0] is Command.MCP)
    check("/config 可识别", parse("/config")[0] is Command.CONFIG)

    names = [item.name for item in suggest("/co")]
    check("补全 /co 命中 compact+config", names == ["/compact", "/config"], str(names))
    check("补全 /re 命中 resume", [s.name for s in suggest("/re")] == ["/resume"])
    check("补全不带 / 时为空", suggest("co") == [])
    check("补全空前缀为空", suggest("/")[:1] and len(suggest("/")) > 5)


# -- 2. 配置段与默认值 ---------------------------------------------------------
def test_config_sections(tmp: Path) -> None:
    path = tmp / "custom.ini"
    path.write_text(
        "\n".join([
            "[llm]",
            "base_url = http://example.test",
            "api_key = sk-test",
            "model_name = m1",
            "[search]",
            "default_top_k = 8",
            "max_snippet_chars = 300",
            "[tools]",
            "fetch_timeout = 12",
            "cli_max_chars = 345",
            "recall_limit = 7",
            "[mcp]",
            "connect_timeout = 5",
            "call_timeout = 9",
        ]),
        encoding="utf-8",
    )
    settings = load_settings(str(path))
    check("[tools] fetch_timeout 生效", settings.fetch_timeout == 12)
    check("[tools] cli_max_chars 生效", settings.cli_max_chars == 345)
    check("[tools] recall_limit 生效", settings.recall_limit == 7)
    check("[mcp] connect_timeout 生效", settings.mcp_connect_timeout == 5)
    check("[mcp] call_timeout 生效", settings.mcp_call_timeout == 9)
    check("[search] default_top_k 生效", settings.search_top_k == 8)
    check("[search] max_snippet_chars 生效", settings.search_snippet_chars == 300)
    check("未配置的 [tools] 项回落默认值", settings.fetch_max_chars == 20000)

    bad = tmp / "bad.ini"
    bad.write_text("[llm]\nbase_url = http://x\n[tools]\ncli_timeout = 0\n",
                   encoding="utf-8")
    try:
        load_settings(str(bad))
    except ConfigError as exc:
        check("非法 [tools] 数值报 ConfigError", "cli_timeout" in str(exc), str(exc))
    else:
        check("非法 [tools] 数值报 ConfigError", False, "未抛异常")


def test_bootstrap(tmp: Path) -> None:
    workdir = tmp / "first-run"
    workdir.mkdir()
    previous_env = os.environ.pop("ZAGENT_CONFIG", None)
    try:
        with _chdir(workdir):
            settings = load_settings()
    finally:
        if previous_env is not None:
            os.environ["ZAGENT_CONFIG"] = previous_env

    created = workdir / "config.ini"
    check("首次运行自动生成 config.ini", created.is_file())
    text = created.read_text(encoding="utf-8")
    check("生成的配置含 mock api_key", "sk-mock-please-replace-me" in text)
    check("生成的配置含 [tools] / [mcp] 段", "[tools]" in text and "[mcp]" in text)
    check("生成后返回 startup_notice", bool(settings.startup_notice))
    check("startup_notice 提醒重新启动", "重新启动" in settings.startup_notice)
    check("配置路径指向新文件", settings.config_path == str(created))

    # 第二次运行不再生成提示
    previous_env = os.environ.pop("ZAGENT_CONFIG", None)
    try:
        with _chdir(workdir):
            again = load_settings()
    finally:
        if previous_env is not None:
            os.environ["ZAGENT_CONFIG"] = previous_env
    check("再次启动不再提示", again.startup_notice == "")

    # 模板本身完整（可被 configparser 解析）
    tpl = tmp / "template.ini"
    write_default_config(tpl)
    check("默认模板写入成功", tpl.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEXT)


# -- 3. 工具参数注入 -----------------------------------------------------------
def test_tool_params() -> None:
    registry = build_default_registry(
        "./diagrams",
        project_root=str(ROOT),
        search_top_k=7,
        search_snippet_chars=222,
        fetch_timeout=11,
        fetch_max_bytes=1024 * 1024,
        fetch_max_chars=1234,
        file_read_chars=567,
        file_list_entries=89,
        cli_timeout=33,
        cli_max_timeout=44,
        cli_max_chars=45,
    )
    fetch = registry.get("fetchWeb")
    check(
        "fetchWeb 使用配置的超时/长度",
        fetch.handler.keywords["timeout"] == 11
        and fetch.handler.keywords["max_chars"] == 1234
        and fetch.handler.keywords["max_bytes"] == 1024 * 1024,
        str(fetch.handler.keywords),
    )
    search = registry.get("searchWeb")
    check(
        "searchWeb 使用配置的条数/摘要长度",
        search.handler.keywords["top_k"] == 7
        and search.handler.keywords["snippet_chars"] == 222,
        str(search.handler.keywords),
    )
    cli = registry.get("RunCli")
    check(
        "RunCli 使用配置的超时/截断",
        cli.handler.keywords["default_timeout"] == 33
        and cli.handler.keywords["max_timeout"] == 44
        and cli.handler.keywords["max_chars"] == 45,
        str(cli.handler.keywords),
    )
    check("RunCli 描述包含配置值", "45" in cli.description, cli.description)
    files = registry.get("file0ps")
    check(
        "file0ps 使用配置的读取/列表上限",
        files.handler.keywords["max_read_chars"] == 567
        and files.handler.keywords["max_list_entries"] == 89,
        str(files.handler.keywords),
    )
    check("file0ps 描述包含配置值", "567" in files.description, files.description)


# -- 4. AGENTS.md --------------------------------------------------------------
def test_agents_md(tmp: Path) -> None:
    project = tmp / "proj"
    project.mkdir()
    check("没有 AGENTS.md 时返回 None", load_agents_md(project) is None)

    (project / "AGENTS.md").write_text("# 项目规范\n\n- 使用中文回答\n",
                                       encoding="utf-8")
    content = load_agents_md(project)
    check("读取 AGENTS.md 内容", bool(content) and "使用中文回答" in content)
    message = build_message(content or "")
    check("注入消息带固定标记", message["content"].startswith(MARKER))
    check("注入消息角色为 user", message["role"] == "user")

    history = [{"role": "system", "content": "SYS"}]
    check("ensure_injected 插入成功", ensure_injected(history, content))
    check("插入位置在 System Prompt 之后", find_index(history) == 1, str(history))
    check("重复调用不再注入", not ensure_injected(history, content))
    check("/compact 后仍只有一条规范消息",
          sum(1 for item in history if item.get("content", "").startswith(MARKER)) == 1)

    # 会话构造时一次注入；conversation_count 不把规范算作对话
    settings = _settings()
    session = AgentSession(
        _Client([]), ToolRegistry(), settings, "SYS",
        agents_md=content,
    )
    check("会话构造即注入 AGENTS.md", find_index(session.history) == 1)
    check("空对话的 conversation_count 为 0", session.conversation_count == 0)
    check("message_count 包含规范消息", session.message_count == 2)

    # 恢复历史：已有规范消息时不重复插入
    session.load_history(list(session.history))
    check("恢复历史不重复注入", session.message_count == 2, str(session.message_count))

    # 恢复旧会话（没有规范消息）时补一次
    legacy = AgentSession(_Client([]), ToolRegistry(), settings, "SYS",
                          agents_md=content)
    legacy.load_history([
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ])
    check("恢复旧会话补注入 AGENTS.md", find_index(legacy.history) == 1)
    check("旧会话 conversation_count 仍为 2", legacy.conversation_count == 2)

    # 压缩后保留 AGENTS.md（摘要替换历史但不丢规范）
    compact_session = AgentSession(
        _Client([StreamResult(content="## 摘要\n压缩后的工作摘要")]),
        ToolRegistry(), settings, "SYS", agents_md=content,
        context_dir=tmp / "archive",
    )
    compact_session.load_history([
        {"role": "user", "content": "任务一"},
        {"role": "assistant", "content": "完成一"},
    ])
    events = [event for event in compact_session.compact()]
    end = next((e for e in events if isinstance(e, CompactEnd)), None)
    check("压缩成功", end is not None and end.ok, str(end))
    check("压缩后保留 AGENTS.md", find_index(compact_session.history) == 1,
          str(compact_session.history[:2]))
    check("压缩后历史 = system + 规范 + 摘要", compact_session.message_count == 3,
          str(compact_session.message_count))

    # 内容为空白时不注入
    empty = [{"role": "system", "content": "SYS"}]
    check("空白内容不注入", not ensure_injected(empty, "   "))


# -- 5. 会话保存 / 恢复 / 导出 --------------------------------------------------
def test_persistence(tmp: Path) -> None:
    workdir = tmp / "workspace"
    workdir.mkdir()
    with _chdir(workdir):
        service = AgentService(_settings(), mcp_async=False)
        try:
            session = service.new_session()
            session.load_history([
                {"role": "user", "content": "帮我看看 config"},
                {"role": "assistant", "content": "好的，已检查"},
            ])
            record = service.save_session(session.history, title="配置排查")
            check("save_session 创建记录", bool(record.id) and record.message_count == 2)
            check("会话文件已写入", (service.sessions.directory / f"{record.id}.json").is_file())

            opened = service.open_session(record.id)
            check("open_session 恢复历史", opened is not None and opened.message_count == 3)
            check("恢复会话绑定持久化", opened is not None and opened.session_id == record.id)

            # 更新同一条记录（/save 第二次）
            session.load_history([
                {"role": "user", "content": "帮我看看 config"},
                {"role": "assistant", "content": "好的，已检查"},
                {"role": "user", "content": "再补充一句"},
            ])
            updated = service.save_session(session.history, record_id=record.id)
            check("按 record_id 更新记录", updated.id == record.id)
            check("更新后消息数增加", updated.message_count == 3, str(updated.message_count))

            markdown = history_to_markdown(
                session.history, title="配置排查", model="fake-model"
            )
            check("导出 Markdown 含标题", "# Zagent 会话导出" in markdown)
            check("导出 Markdown 含消息块", "## 用户" in markdown and "## Zagent" in markdown)

            # MCP 异步加载：进入界面后等待完成
            async_service = AgentService(_settings(), mcp_async=True)
            try:
                async_service.wait_mcp(timeout=20)
                check("wait_mcp 后不再处于加载中", not async_service.mcp_loading)
                check("异步服务状态可查询", isinstance(async_service.mcp_statuses(), list))
            finally:
                async_service.shutdown()
            check("同步服务默认不在加载中", not service.mcp_loading)
            check(
                "describe 暴露 mcp_loading / agents_md",
                "mcp_loading" in service.describe() and "agents_md" in service.describe(),
            )
        finally:
            service.shutdown()


def main() -> int:
    print("=" * 64)
    print(" Zagent 会话管理 / 配置化 / AGENTS.md / MCP 异步 自测")
    print("=" * 64)
    tmp = Path(tempfile.mkdtemp(prefix="zagent-extras-"))
    try:
        for title, func in (
            ("斜杠命令", test_commands),
            ("配置段", lambda: test_config_sections(tmp)),
            ("首次运行生成配置", lambda: test_bootstrap(tmp)),
            ("工具参数注入", test_tool_params),
            ("AGENTS.md 注入", lambda: test_agents_md(tmp)),
            ("会话保存 / 恢复 / MCP 异步", lambda: test_persistence(tmp)),
        ):
            print(f"\n[{title}]")
            func()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 64)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
