# -*- coding: utf-8 -*-
"""工具（Function Call）层：定义、注册与分发。

- :class:`ToolSpec`      一个工具的完整描述（schema + 处理函数）
- :class:`ToolRegistry`  工具注册表 + 分发器（core 与模型之间的唯一桥梁）

新增工具只需实现处理函数并 register，一处注册即可同时被
schema 列表（给模型）与分发逻辑（执行）使用，避免两处维护。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..errors import ToolError

#: 工具处理函数：接收与 schema 参数同名的关键字参数，返回给模型的文本结果
ToolHandler = Callable[..., str]


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的静态描述。"""

    name: str
    description: str
    parameters: dict  # JSON Schema (type=object)
    handler: ToolHandler

    @property
    def schema(self) -> dict:
        """OpenAI Function Calling 格式的 tools 条目。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表：登记 -> 生成 schema -> 按名分发执行。"""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    # -- 注册 ---------------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolError(f"工具重复注册 -> {spec.name}")
        self._tools[spec.name] = spec

    def names(self) -> List[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    # -- 模型侧 -------------------------------------------------------------
    def schemas(self) -> List[dict]:
        """传给 LLM 的 tools 列表。"""
        return [spec.schema for spec in self._tools.values()]

    # -- 执行 ---------------------------------------------------------------
    def execute(self, name: str, arguments: Optional[dict] = None) -> str:
        """按工具名分发执行，返回回传给模型的字符串结果。

        预期内的错误（参数非法、文件不存在等）被捕获并转成
        ``"错误：..."`` 文本回传给模型；未知工具同样以错误文本回复，
        避免一次坏调用让整个会话崩溃。
        """
        spec = self._tools.get(name)
        if spec is None:
            return f"错误：未知工具 {name}"

        args = dict(arguments or {})
        try:
            return spec.handler(**args)
        except TypeError as exc:
            return f"错误：工具 {name} 参数不匹配 -> {exc}"
        except (ValueError, OSError, KeyError) as exc:
            return f"错误：{exc}"


__all__ = ["ToolHandler", "ToolSpec", "ToolRegistry"]
