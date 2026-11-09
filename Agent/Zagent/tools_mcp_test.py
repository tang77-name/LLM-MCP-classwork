# -*- coding: utf-8 -*-
"""MCP 客户端集成自测（不调用 LLM，直接驱动工具注册与转发链路）。

用法::

    python tools_mcp_test.py

覆盖：
1. 读取项目根目录 ``.mcp.json``；
2. 连接 Server + ``list_tools()`` 动态注册（与本地工具共存）；
3. 走 ``ToolRegistry.execute``（与 LLM Function Call 完全同一条路径）转发调用；
4. 结构化数据 / 错误信息处理；
5. 失败 Server 不影响其它工具（错误信息清晰）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.mcp import MCPManager, load_mcp_config  # noqa: E402
from core.tools import ToolSpec, build_default_registry  # noqa: E402

LOCAL_TOOLS = ("RunCli", "fetchWeb", "getLocalDateTime")
FAILED = 0


def check(title: str, ok: bool, detail: str = "") -> bool:
    global FAILED
    mark = "PASS" if ok else "FAIL"
    if not ok:
        FAILED += 1
    print(f"[{mark}] {title}" + (f" -> {detail}" if detail else ""))
    return ok


def main() -> int:
    config_path = ROOT / ".mcp.json"
    servers, error = load_mcp_config(config_path)
    check("1. 读取 .mcp.json", bool(servers) and not error,
          json.dumps({k: v.target for k, v in servers.items()}, ensure_ascii=False)
          + (f" | 错误: {error}" if error else ""))

    registry = build_default_registry("./diagrams", project_root=str(ROOT))
    before = set(registry.names())
    check("2. 本地工具基线", all(t in before for t in LOCAL_TOOLS),
          ", ".join(sorted(before)))

    mcp = MCPManager(config_path)
    try:
        statuses = mcp.connect(registry)
        rows = [s.as_dict() for s in statuses]
        print("     MCP 状态:", json.dumps(rows, ensure_ascii=False, indent=2))

        connected = [s for s in statuses if s.ok]
        check("3. 连接 MCP Server", bool(connected),
              "；".join(s.summary() for s in statuses))

        added = sorted(set(registry.names()) - before)
        check("4. 动态注册 MCP 工具", bool(added),
              f"{len(added)} 个: {', '.join(added[:8])}"
              + (" ..." if len(added) > 8 else ""))

        # 本地工具仍可正常执行（MCP 接入不破坏现有工具）
        local_out = registry.execute("RunCli", {"command": "python", "args": ["--version"]})
        check("5. 本地工具不受影响", "Python" in local_out, local_out.strip()[:80])

        if not added:
            return finish("无可用 MCP 工具，跳过调用链路测试")

        target = added[0]
        spec = registry.get(target)
        arguments = build_arguments(spec.parameters or {})
        print(f"     调用 {target}({json.dumps(arguments, ensure_ascii=False)})")
        output = registry.execute(target, arguments) or ""
        print("     结果:", output[:800])
        check("6. 转发调用（Function Call 路径）",
              bool(output) and not output.startswith("错误：MCP"),
              f"{len(output)} 字符")

        # 错误分支：未知工具、未知参数
        unknown = registry.execute("mcp_not_exist_tool", {})
        check("7. 未知工具返回错误文本", unknown.startswith("错误："), unknown[:120])
        bad = registry.execute(target, {"/invalid key": 1})
        check("8. 非法参数返回可读结果", isinstance(bad, str) and bool(bad),
              bad[:160])
    finally:
        mcp.close()

    isolation_test()
    return finish("全部完成" if not FAILED else f"{FAILED} 项失败")


def isolation_test() -> None:
    """故障隔离 + stdio 传输：坏 Server / 好 Server / 本地工具共存。"""
    import shutil
    import tempfile

    print("-" * 60)
    tmp = Path(tempfile.mkdtemp(prefix="zagent-mcp-"))
    try:
        config_path = tmp / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "down-server": {
                            "type": "streamable_http",
                            "url": "http://127.0.0.1:9/mcp",
                            "timeout": 3,
                        },
                        "demo-stdio": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [str(ROOT / "examples" / "mcp_demo_server.py")],
                            "cwd": str(ROOT),
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        registry = build_default_registry("./diagrams", project_root=str(ROOT))
        # 预置同名本地工具，验证“重名时自动加服务器名前缀”
        for clash in ("demo_echo", "demo_add"):
            registry.register(ToolSpec(
                name=clash,
                description="本地同名工具（用于验证 MCP 工具名冲突处理）",
                parameters={"type": "object", "properties": {}},
                handler=lambda **_: "本地同名工具被执行",
            ))
        before = set(registry.names())
        mcp = MCPManager(config_path, connect_timeout=20)
        try:
            statuses = {s.name: s for s in mcp.connect(registry)}
            print("     状态:",
                  json.dumps([s.as_dict() for s in statuses.values()],
                             ensure_ascii=False, indent=2))

            down = statuses.get("down-server")
            check("9. 不可达 Server 报错且不中断启动",
                  down is not None and not down.ok and bool(down.error),
                  (down.error if down else "缺少状态记录")[:160])

            demo = statuses.get("demo-stdio")
            check("10. stdio Server 正常连接", bool(demo and demo.ok),
                  demo.summary() if demo else "缺少状态记录")

            added = sorted(set(registry.names()) - before)
            check("11. 故障隔离：好 Server 的工具仍注册", bool(added),
                  ", ".join(added))

            local_out = registry.execute("RunCli", {"command": "python", "args": ["--version"]})
            check("12. 故障隔离：本地工具可用", "Python" in local_out,
                  local_out.strip()[:80])

            echo_name = next((n for n in added if "echo" in n), "")
            out = registry.execute(echo_name, {"text": "你好"}) if echo_name else ""
            check("13. stdio 工具转发（文本）", out.startswith("echo:"), out[:120])

            add_name = next((n for n in added if "add" in n), "")
            out2 = registry.execute(add_name, {"a": 2, "b": 3}) if add_name else ""
            check("14. stdio 工具转发（结构化数据）",
                  "5" in out2 and "sum" in out2, out2[:200])

            # 名字冲突：MCP 工具必须带服务器名前缀，且仍转发给 Server
            prefixed = sorted(n for n in added if n.startswith("demo-stdio"))
            check("15. 重名工具自动加服务器前缀",
                  len(prefixed) == 2, ", ".join(prefixed))
            forwarded = registry.execute(echo_name, {"text": "前缀"}) if echo_name else ""
            check("16. 前缀工具转发到 MCP Server（而非本地同名工具）",
                  forwarded.startswith("echo:"), forwarded[:80])

            # 配置文件本身非法时：给出清晰错误，不影响本地工具
            config_path.write_text("{ 这不是 JSON", encoding="utf-8")
            bad_registry = build_default_registry("./diagrams",
                                                  project_root=str(ROOT))
            bad_mcp = MCPManager(config_path)
            try:
                bad_status = bad_mcp.connect(bad_registry)
                check("17. 配置非法给出明确错误",
                      len(bad_status) == 1 and not bad_status[0].ok
                      and "JSON" in bad_status[0].error,
                      bad_status[0].error if bad_status else "无状态")
                check("18. 配置非法不影响本地工具",
                      all(t in bad_registry.names() for t in LOCAL_TOOLS),
                      ", ".join(bad_registry.names()))
            finally:
                bad_mcp.close()
        finally:
            mcp.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: 关键字 -> 样例值（覆盖常见入参语义，保证一次即可调通）
_SAMPLE_HINTS = (
    ("solardatetime", "1990-03-01T13:00:00+08:00"),
    ("lunardatetime", "1990-3-1 12:00:00"),
    ("bazi", "庚午 己卯 庚申 丙子"),
    ("gender", 1),
    ("birthday", "1990-03-01T13:00:00+08:00"),
    ("date", "1990-03-01"),
    ("datetime", "1990-03-01T13:00:00+08:00"),
    ("time", "12:00:00"),
    ("question", "测试"),
    ("name", "张三"),
)


def _looks_datetime(key: str, schema: dict) -> bool:
    """判断可选参数是否是日期/时间类（这类参数服务端常有隐含必填要求）。"""
    text = f"{key} {schema.get('description', '')}".lower()
    return any(token in text for token in ("date", "time", "日期", "时间"))


def build_arguments(params: dict) -> dict:
    """按 JSON Schema 组装一组最小可运行参数：必填项 + 日期时间类可选项。"""
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    arguments = {}
    for key, schema in props.items():
        if key not in required and not _looks_datetime(key, schema or {}):
            continue
        value = _sample_value(key, schema or {})
        if value is not None:
            arguments[key] = value
    return arguments


def _sample_value(name: str, schema: dict) -> object:
    """按 JSON Schema 造一个最小可运行的参数值。"""
    for hint, value in _SAMPLE_HINTS:
        if hint in name.lower():
            return value
    stype = schema.get("type")
    if stype == "string":
        return schema.get("default", "1990-01-01 12:00:00")
    if stype in ("number", "integer"):
        return schema.get("default", 1)
    if stype == "boolean":
        return schema.get("default", True)
    if stype == "array":
        return schema.get("default", [])
    if stype == "object":
        return schema.get("default", {})
    return schema.get("default", "1990-01-01")


def finish(message: str) -> int:
    print("=" * 60)
    print(("失败：" if FAILED else "成功：") + message)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
