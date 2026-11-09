# -*- coding: utf-8 -*-
"""OpenAI 兼容协议的流式 Chat 客户端。

设计要点：

- :class:`ChatClient` 只负责“发请求 + 解析 SSE”，**不打印任何内容**，
  文本增量以 :class:`core.events.TextDelta` 事件的形式交给调用方；
- :class:`ChatStream` 是惰性可迭代对象：开始 ``for ... in stream`` 才发起网络
  请求，迭代完毕后通过 ``stream.result`` 取回汇总结果；
- 网络 / 协议错误统一包装为 core 层异常（:class:`LLMNetworkError` /
  :class:`LLMResponseError`），UI 无需理解 requests 的异常体系。
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator, List, Optional

import requests

from ..errors import LLMNetworkError, LLMResponseError
from ..events import Event, TextDelta
from .types import ChatMessage, StreamResult, ToolCall


class ChatStream:
    """一次流式响应的可迭代封装。

    迭代过程中逐个产出 :class:`TextDelta`；迭代结束后 ``result`` 属性给出
    完整文本、拼接好的工具调用列表与 ``finish_reason``。
    """

    def __init__(
        self,
        url: str,
        payload: dict,
        headers: dict,
        timeout: int,
    ) -> None:
        self._url = url
        self._payload = payload
        self._headers = headers
        self._timeout = timeout
        self._result: Optional[StreamResult] = None

    @property
    def result(self) -> StreamResult:
        """汇总结果；必须在迭代完成后访问。"""
        if self._result is None:
            raise LLMNetworkError("流尚未消费完毕，无法获取 result")
        return self._result

    def __iter__(self) -> Iterator[Event]:
        return self._consume()

    # -- HTTP ---------------------------------------------------------------
    def _post(self) -> requests.Response:
        """发起 SSE 请求并做状态码检查（必要时自动降级重试一次）。

        部分 OpenAI 兼容服务不认识 ``stream_options``（会回 HTTP 400），
        此时自动去掉该字段重发一次 —— 拿不到 API 用量就退回估算，不能因此
        让整个请求失败。
        """
        resp = requests.post(
            self._url,
            json=self._payload,
            headers=self._headers,
            stream=True,
            timeout=self._timeout,
        )
        if resp.status_code == 200:
            return resp

        body = resp.text[:500]
        resp.close()
        if (
            resp.status_code == 400
            and "stream_options" in self._payload
            and "stream_options" in body
        ):
            self._payload.pop("stream_options", None)
            resp = requests.post(
                self._url,
                json=self._payload,
                headers=self._headers,
                stream=True,
                timeout=self._timeout,
            )
            if resp.status_code == 200:
                return resp
            body = resp.text[:500]
            resp.close()
        raise LLMResponseError(f"请求失败 HTTP {resp.status_code}: {body}")

    # -- SSE 解析 -----------------------------------------------------------
    def _consume(self) -> Iterator[Event]:
        content_parts: List[str] = []
        # 按 tool_call 索引收集增量片段: {index: {"id","name","arguments"}}
        tool_call_buf: dict = {}
        finish_reason: Optional[str] = None
        usage: Optional[dict] = None

        try:
            resp = self._post()
            with resp:
                # SSE 通常只声明 text/event-stream 而无 charset，
                # requests 默认 ISO-8859-1 解码会导致中文乱码，强制 UTF-8
                resp.encoding = "utf-8"

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue  # SSE 空行
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("error"):
                        raise LLMResponseError(str(chunk["error"]))

                    # API 报告的 token 用量（开启 include_usage 时在末块返回）
                    chunk_usage = chunk.get("usage")
                    if isinstance(chunk_usage, dict) and chunk_usage:
                        usage = chunk_usage

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    text = delta.get("content")
                    if text:
                        content_parts.append(text)
                        yield TextDelta(text)

                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        entry = tool_call_buf.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        fn = tc.get("function") or {}
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        if fn.get("name"):
                            entry["name"] += fn["name"]
                        if fn.get("arguments"):
                            entry["arguments"] += fn["arguments"]

                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        except requests.Timeout as exc:
            raise LLMNetworkError(f"请求超时（{self._timeout}s）: {exc}") from exc
        except requests.RequestException as exc:
            raise LLMNetworkError(str(exc)) from exc

        tool_calls: List[ToolCall] = [
            tool_call_buf[i] for i in sorted(tool_call_buf)  # type: ignore[misc]
        ]
        self._result = StreamResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )


class ChatClient:
    """无状态的流式聊天客户端（可被多个会话共享）。"""

    def __init__(
        self,
        chat_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.7,
        timeout: int = 60,
    ) -> None:
        self._chat_url = chat_url
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    @property
    def chat_url(self) -> str:
        return self._chat_url

    def stream(
        self,
        messages: Iterable[ChatMessage],
        tools: Optional[List[dict]] = None,
    ) -> ChatStream:
        """构造一次流式请求（惰性，不立即联网）。

        :param messages: 完整消息历史
        :param tools: OpenAI 格式的 tools schema 列表
        """
        payload: dict = {
            "model": self._model,
            "messages": list(messages),
            "stream": True,
            "temperature": self._temperature,
            # 请求 API 报告 token 用量（上下文监控优先使用；
            # 服务不支持时 ChatStream 会自动降级去掉该字段重试）
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return ChatStream(self._chat_url, payload, headers, self._timeout)


__all__ = ["ChatClient", "ChatStream"]
