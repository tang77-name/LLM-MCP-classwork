# -*- coding: utf-8 -*-
"""网络搜索工具（searchWeb）。

调用百度千帆“百度搜索”API（``POST /v2/ai_search/web_search``），把实时
网页搜索结果整理成“摘要 + 来源标题 + URL”的纯文本返回给模型。纯业务
实现：不打印、不依赖界面。

鉴权：API Key 通过 ``config.ini`` 的 ``[search]`` 段注入，本模块不读取
全局配置、不硬编码密钥。
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, Dict, List, Optional

import requests

from .base import ToolRegistry, ToolSpec

#: 百度千帆“百度搜索”API
WEB_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
#: 默认返回网页条数
DEFAULT_TOP_K = 10
#: API 允许的最大网页条数
MAX_TOP_K = 50
#: 默认网络超时（秒）
DEFAULT_TIMEOUT = 20
#: 每条结果摘要的最大字符数（避免一次工具结果占用过多上下文）
MAX_SNIPPET_CHARS = 600

MISSING_KEY_MESSAGE = (
    "错误：未配置百度千帆 API Key，无法使用网络搜索。"
    "请在 config.ini 的 [search] 段设置 baidu_qianfan_api_key=...，"
    "或设置环境变量 BAIDU_QIANFAN_API_KEY。"
)


def _clean_text(value: Any, limit: int) -> str:
    """压缩空白、去除控制字符，并按最大长度截断。"""
    if value is None:
        return ""
    text = str(value)
    # 搜索内容中可能含 \u0004 / \u0005 等控制字符
    text = text.replace("\u0004", " ").replace("\u0005", " ")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _api_error(status: int, data: Any) -> Optional[str]:
    """把 API 错误响应整理成给模型看的清晰文本；成功时返回 None。"""
    if isinstance(data, dict):
        code = data.get("code")
        message = data.get("message") or data.get("msg") or data.get("error_msg")
        request_id = data.get("request_id") or data.get("requestId")
    else:
        code = None
        message = str(data or "")[:300]
        request_id = None

    # 成功响应可能没有 code，也可能显式返回 0 / 200
    if status == 200 and code in (None, "", 0, "0", 200, "200"):
        return None

    lines = [f"错误：百度千帆搜索 API 请求失败（HTTP {status}）"]
    if code not in (None, ""):
        lines.append(f"错误码：{code}")
    if message:
        lines.append(f"错误信息：{message}")

    code_text = str(code) if code not in (None, "") else ""
    if status == 401 or code_text == "216003":
        lines.append("可能原因：API Key 无效或鉴权头格式错误，请检查 [search] 配置。")
    elif status == 403:
        lines.append("可能原因：当前 API Key 没有百度搜索权限，或账号未开通该服务。")
    elif status == 429:
        lines.append("可能原因：请求过于频繁或免费额度已用完，请稍后重试。")
    elif status >= 500:
        lines.append("可能原因：百度千帆服务暂时不可用，请稍后重试。")

    if request_id:
        lines.append(f"请求 ID：{request_id}")
    return "\n".join(lines)


def search_web(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    *,
    api_key: str,
    endpoint: str = WEB_SEARCH_URL,
    timeout: int = DEFAULT_TIMEOUT,
    snippet_chars: int = MAX_SNIPPET_CHARS,
) -> str:
    """searchWeb：调用百度千帆搜索 API，返回整理后的搜索结果文本。

    :param query: 搜索关键词或问题，不能为空。
    :param top_k: 返回网页条数，1..50，默认 10。
    :param api_key: 百度千帆 API Key（由配置注入，不在此处读取配置）。
    :param endpoint: 搜索 API 地址。
    :param timeout: 网络超时（秒）。
    :param snippet_chars: 单条摘要的最大字符数（config.ini ``[search] max_snippet_chars``）。
    """
    keyword = str(query or "").strip()
    if not keyword:
        raise ValueError("query 不能为空，请提供搜索关键词或问题")

    try:
        limit = int(top_k)
    except (TypeError, ValueError) as exc:
        raise ValueError("top_k 必须是 1..50 之间的整数") from exc
    if limit < 1 or limit > MAX_TOP_K:
        raise ValueError(f"top_k 需在 1..{MAX_TOP_K} 之间 -> {limit}")

    key = (api_key or "").strip()
    if not key:
        return MISSING_KEY_MESSAGE

    payload: Dict[str, Any] = {
        "messages": [{"role": "user", "content": keyword}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": limit}],
        "query_policy": {"enable_rewrite": True},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.Timeout:
        return (
            f"错误：网络搜索请求超时（{timeout} 秒），"
            "请稍后重试或缩短搜索关键词。"
        )
    except requests.RequestException as exc:
        return f"错误：网络搜索请求失败（无法连接百度千帆 API）-> {exc}"

    try:
        data = json.loads(resp.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = _clean_text(resp.text, 300)
        hint = ""
        if resp.status_code == 429:
            hint = "\n可能原因：请求过于频繁或免费额度已用完，请稍后重试。"
        elif resp.status_code >= 500:
            hint = "\n可能原因：百度千帆服务暂时不可用，请稍后重试。"
        return (
            f"错误：百度千帆搜索 API 请求失败（HTTP {resp.status_code}），"
            f"返回内容无法解析为 JSON"
            f"{'：' + body if body else ''}{hint}"
        )

    error = _api_error(resp.status_code, data)
    if error:
        return error

    if not isinstance(data, dict):
        return "错误：搜索 API 返回格式异常（顶层不是 JSON 对象）"

    references = data.get("references") or []
    if not isinstance(references, list):
        return "错误：搜索 API 返回格式异常（references 不是数组）"

    rows: List[dict] = []
    seen = set()
    for ref in references:
        if not isinstance(ref, dict):
            continue
        url = str(ref.get("url") or "").strip()
        title = _clean_text(ref.get("title"), 200)
        if not url and not title:
            continue
        dedup_key = url or title
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        rows.append(ref)

    if not rows:
        return f"未找到与「{keyword}」相关的网页搜索结果。可尝试更换关键词或稍后重试。"

    lines = [
        f"搜索关键词：{keyword}",
        f"搜索结果：共 {len(rows)} 条（来自百度千帆搜索）",
        "",
    ]
    for index, ref in enumerate(rows, 1):
        title = _clean_text(ref.get("title"), 200) or "（无标题）"
        url = str(ref.get("url") or "").strip()
        snippet = _clean_text(
            ref.get("snippet") or ref.get("content"), snippet_chars
        )
        date = _clean_text(ref.get("date"), 40)

        lines.append(f"[{index}] {title}")
        if date:
            lines.append(f"日期：{date}")
        if snippet:
            lines.append(f"摘要：{snippet}")
        lines.append(f"来源：{url or '（无 URL）'}")
        lines.append("")

    lines.append("请优先依据上述摘要作答，并在引用时注明来源标题与 URL。")
    return "\n".join(lines)


def build_registry(
    *,
    api_key: str,
    timeout: int = DEFAULT_TIMEOUT,
    default_top_k: int = DEFAULT_TOP_K,
    snippet_chars: int = MAX_SNIPPET_CHARS,
    endpoint: str = WEB_SEARCH_URL,
) -> ToolRegistry:
    """装配网络搜索工具组。

    :param api_key: 百度千帆 API Key（由配置注入）。
    :param timeout: 搜索请求超时（秒）。
    :param default_top_k: 模型未指定 ``top_k`` 时的默认条数（config.ini）。
    :param snippet_chars: 单条摘要最大字符数（config.ini）。
    :param endpoint: 搜索 API 地址。
    """
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="searchWeb",
        description=(
            "联网搜索互联网上的最新信息。当用户询问实时新闻、当前事件、"
            "最新数据、产品版本、天气/股价等模型知识可能过时的内容时调用。"
            "query 应为简洁的搜索关键词或一句话问题（百度接口限 72 个字符，"
            "汉字按 2 个字符计，建议控制在 30 个汉字以内）；"
            "工具会返回搜索摘要、参考来源标题与 URL，回答时可据此注明来源。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题，例如 '2026 年诺贝尔物理学奖'。",
                },
                "top_k": {
                    "type": "integer",
                    "description": f"可选。返回的网页结果条数，1..50，默认 {default_top_k}。",
                    "minimum": 1,
                    "maximum": MAX_TOP_K,
                    "default": default_top_k,
                },
            },
            "required": ["query"],
        },
        handler=partial(
            search_web,
            api_key=api_key,
            endpoint=endpoint,
            timeout=timeout,
            top_k=default_top_k,
            snippet_chars=snippet_chars,
        ),
    ))
    return registry


__all__ = [
    "WEB_SEARCH_URL",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "DEFAULT_TIMEOUT",
    "MISSING_KEY_MESSAGE",
    "search_web",
    "build_registry",
]
