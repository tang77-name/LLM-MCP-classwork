# -*- coding: utf-8 -*-
"""网页正文抓取工具（fetchWeb）。

流程：

1. 通过 :mod:`subprocess` 以**参数列表**方式调用本地 ``curl``（不使用
   ``shell=True``）下载网页 HTML；
2. 用 :mod:`trafilatura` 识别正文，并输出 Markdown；
3. 保留标题、段落、列表、表格和链接，去掉导航栏、广告、页脚等无关内容；
4. 返回 Markdown 正文与原始 URL。

纯业务实现：不打印、不依赖界面，也不读取全局配置。
"""

from __future__ import annotations

import codecs
import re
import shutil
import subprocess
from functools import partial
from typing import Tuple
from urllib.parse import urlparse

from .base import ToolRegistry, ToolSpec

#: 网页下载超时（秒）
FETCH_TIMEOUT = 30
#: 建立连接的超时（秒）
CONNECT_TIMEOUT = 15
#: HTML 源代码大小上限（8 MB），防止异常大页面占用内存
MAX_HTML_BYTES = 8 * 1024 * 1024
#: 返回给模型的 Markdown 最大字符数
MAX_MARKDOWN_CHARS = 20000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

#: 常见 curl 退出码 -> 中文说明
_CURL_ERRORS = {
    6: "无法解析域名",
    7: "无法连接到服务器",
    22: "服务器返回 HTTP 错误（如 4xx/5xx）",
    28: "请求超时",
    35: "SSL/TLS 握手失败",
    60: "SSL 证书验证失败",
    63: "网页体积超过限制",
}


def _normalize_url(url: str) -> str:
    """校验并返回原始 URL（仅允许 http/https）。"""
    target = str(url or "").strip()
    if not target:
        raise ValueError("url 不能为空")
    parsed = urlparse(target)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"url 必须以 http:// 或 https:// 开头 -> {target}")
    if not parsed.netloc:
        raise ValueError(f"url 格式不合法（缺少域名）-> {target}")
    return target


def _decode_html(raw: bytes) -> str:
    """尽量准确地解码 HTML 字节流（支持 UTF-8 / GBK / Big5 等）。"""
    if not raw:
        return ""
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")

    head = raw[:8192].decode("ascii", errors="ignore")
    match = re.search(
        r"charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)", head, re.IGNORECASE
    )
    declared = match.group(1).lower().replace("_", "-") if match else ""
    declared = {
        "utf8": "utf-8",
        "gb2312": "gb18030",
        "gbk": "gb18030",
        "x-gbk": "gb18030",
    }.get(declared, declared)

    cjk_multibyte = {"gb18030", "big5", "big5-hkscs", "shift-jis", "euc-jp", "euc-kr"}
    if declared in cjk_multibyte:
        candidates = [declared, "utf-8", "gb18030"]
    elif declared in ("", "utf-8", "ascii"):
        candidates = ["utf-8", "gb18030"]
    else:
        # ISO-8859-1 / windows-1252 常被误标，优先尝试 UTF-8
        candidates = ["utf-8", declared, "gb18030"]

    for encoding in dict.fromkeys(candidates):
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _download_html(url: str, timeout: int, max_bytes: int = MAX_HTML_BYTES) -> bytes:
    """调用本地 curl 下载网页 HTML，失败时抛出 ValueError。"""
    curl = shutil.which("curl") or "curl"
    command = [
        curl,
        "--silent",
        "--show-error",
        "--location",
        "--fail",
        "--compressed",
        "--max-time",
        str(timeout),
        "--connect-timeout",
        str(min(CONNECT_TIMEOUT, timeout)),
        "--max-filesize",
        str(max_bytes),
        "--max-redirs",
        "5",
        "--proto",
        "=http,https",
        "--proto-redir",
        "=http,https",
        "--user-agent",
        USER_AGENT,
        "--header",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "--header",
        "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        url,
    ]

    try:
        # 明确使用参数列表且 shell=False，避免命令注入
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout + 5,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "未找到本地 curl 程序，请安装 curl 并确保其位于 PATH 中"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"网页访问超时（超过 {timeout} 秒）") from exc
    except OSError as exc:
        raise ValueError(f"无法执行 curl -> {exc}") from exc

    if proc.returncode != 0:
        reason = _CURL_ERRORS.get(proc.returncode, "curl 执行失败")
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        message = f"网页访问失败（{reason}，curl 退出码 {proc.returncode}）"
        if detail:
            message += f"：{detail[:500]}"
        raise ValueError(message)

    if not proc.stdout:
        raise ValueError("网页返回内容为空（可能为 204 响应或页面无内容）")
    if len(proc.stdout) > max_bytes:
        size = (
            f"{max_bytes // (1024 * 1024)} MB"
            if max_bytes >= 1024 * 1024
            else f"{max_bytes // 1024} KB"
        )
        raise ValueError(
            f"网页源代码过大（超过 {size}），已停止处理"
        )
    return proc.stdout


def _clean_markdown(markdown: str) -> str:
    """清理 Markdown 中的锚点噪声和多余空行。"""
    text = markdown.strip()
    # 去掉标题末尾的 [¶](...) 锚点链接
    text = re.sub(r"\[¶\]\([^)]*\)", "", text)
    # 压缩三个及以上连续换行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_markdown(markdown: str, max_chars: int) -> Tuple[str, bool]:
    """按段落边界截断超长正文；返回 (文本, 是否截断)。"""
    if len(markdown) <= max_chars:
        return markdown, False

    cut = markdown[:max_chars]
    boundary = cut.rfind("\n\n")
    if boundary >= int(max_chars * 0.6):
        cut = cut[:boundary]
    return (
        cut.rstrip()
        + f"\n\n…（正文过长，已截断，仅保留前 {max_chars} 字符）",
        True,
    )


def _extract_markdown(html: bytes, url: str) -> str:
    """用 trafilatura 提取正文并转换为 Markdown。"""
    try:
        import trafilatura
    except ImportError as exc:  # 依赖缺失不应中断整个 Agent
        raise ValueError(
            "缺少 trafilatura 依赖，请执行 pip install -r requirements.txt"
        ) from exc

    text = _decode_html(html)
    try:
        markdown = trafilatura.extract(
            text,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_formatting=True,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )
    except Exception as exc:  # lxml / 解析器异常统一转为工具错误
        raise ValueError(f"HTML 解析失败 -> {type(exc).__name__}: {exc}") from exc

    if not markdown or not markdown.strip():
        raise ValueError(
            "正文提取失败：未从网页中识别到有效正文"
            "（可能是动态渲染页面、登录页、反爬页面或非文章页面）"
        )
    return _clean_markdown(markdown)


def fetch_web_page(
    url: str,
    *,
    timeout: int = FETCH_TIMEOUT,
    max_chars: int = MAX_MARKDOWN_CHARS,
    max_bytes: int = MAX_HTML_BYTES,
) -> str:
    """fetchWeb：抓取网页正文并返回 Markdown + 原始 URL。

    :param url: 要抓取的网页地址（http/https）。
    :param timeout: curl 下载超时（秒）。
    :param max_chars: 返回 Markdown 的最大字符数。
    :param max_bytes: 单页 HTML 下载上限（字节）。
    :raises ValueError: URL 非法、访问失败、超时、解析失败或正文为空。
    """
    target = _normalize_url(url)
    html = _download_html(target, timeout, max_bytes)
    markdown = _extract_markdown(html, target)
    markdown, truncated = _truncate_markdown(markdown, max_chars)

    header = [
        f"网页 URL：{target}",
        "正文格式：Markdown（已去除导航栏、广告、页脚等无关内容）",
    ]
    if truncated:
        header.append(f"提示：正文超过 {max_chars} 字符，已按段落截断。")
    return "\n".join(header) + "\n\n" + markdown


def build_registry(
    *,
    timeout: int = FETCH_TIMEOUT,
    max_chars: int = MAX_MARKDOWN_CHARS,
    max_bytes: int = MAX_HTML_BYTES,
) -> ToolRegistry:
    """装配网页抓取工具组。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="fetchWeb",
        description=(
            "抓取指定网页的正文并转换为 Markdown。"
            "当用户提供网页 URL，或需要阅读某篇文章、公告、文档、资料页的完整内容时调用。"
            "工具会通过本地 curl 获取 HTML，再用 trafilatura 提取正文，"
            "保留标题、段落、列表、表格和链接，去除导航栏、广告、页脚等无关内容，"
            "最终返回 Markdown 正文与原始 URL。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页完整地址，必须以 http:// 或 https:// 开头。",
                }
            },
            "required": ["url"],
        },
        handler=partial(
            fetch_web_page,
            timeout=timeout,
            max_chars=max_chars,
            max_bytes=max_bytes,
        ),
    ))
    return registry


__all__ = [
    "FETCH_TIMEOUT",
    "MAX_MARKDOWN_CHARS",
    "MAX_HTML_BYTES",
    "fetch_web_page",
    "build_registry",
]
