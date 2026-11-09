# -*- coding: utf-8 -*-
"""提示词（Prompt）常量与组装。

与业务绑定的提示词集中在此，未来若支持多套 Agent 人设，可扩展为
``PromptProvider`` 协议并按配置选择。

Skill 采用“元信息常驻、正文按需加载”的方式：初始化时只把各 skill 的
``name`` / ``description`` 追加到 System Prompt，完整内容必须由模型
调用 ``loadSkill`` 工具后才会进入上下文；用户要求创建新 skill 时由
``createSKill`` 生成 ``SKILL.md``，辅助文件交给 ``file0ps``。
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

SYSTEM_PROMPT = (
    "你是一个擅长绘制代码架构图、并能联网检索实时信息的 AI 助手。"
    "当用户描述一个系统、模块或代码的结构时，请使用 Mermaid 语法（flowchart 为主）"
    "生成清晰的架构图，并调用 createMermaidDiagram 工具保存为 .mmd 纯文本文件，"
    "不要只把图画在对话里。"
    "当用户要修改已有图时调用 updateMermaidDiagram，"
    "要查看已有图时调用 listMermaidDiagrams 或 readMermaidDiagram。"
    "生成的 Mermaid 源码必须语法正确：第一行为图类型关键字（如 flowchart TD），"
    "节点 ID 只用字母、数字、下划线，中文标签放在方括号或引号中。"
    "当用户询问当前日期、时间、星期等信息时，调用 getLocalDateTime 获取，"
    "不要凭记忆回答今天是几号。"
    "当问题涉及实时新闻、最新事件、当前数据等训练知识可能过时的内容时，"
    "先调用 searchWeb 联网搜索，再依据返回的摘要作答，"
    "并在回答中注明参考来源的标题与 URL；不要凭记忆编造最新信息。"
    "当用户提供网页 URL，或搜索摘要不足以回答问题、需要阅读文章完整内容时，"
    "调用 fetchWeb 抓取该网页的正文（Markdown），不要猜测网页内容。"
    "当用户要求创建一个新的 skill 时，调用 createSKill 工具生成 "
    ".agents/skills/<name>/SKILL.md，并向它提供规范的名称与完整正文；"
    "如还需要 references/、scripts/ 等辅助目录或文件，继续调用 file0ps 工具创建。"
    "创建前先确认同名 skill 不存在，绝不要覆盖已有 skill。"
)

SKILL_PROMPT_HEADER = "You have access to the following skills:"
SKILL_PROMPT_USAGE = (
    "Use the 'loadSkill' tool to load the full instructions of a skill when needed. "
    "You can load additional files referenced by a skill. "
    "When the user asks to create a new skill, call 'createSKill' with a normalized "
    "name and the full SKILL.md content; use 'file0ps' for extra files such as "
    "references/ or scripts/. Never overwrite an existing skill."
)


def _one_line(text: str) -> str:
    """把多行描述压缩为单行，避免破坏 System Prompt 的列表结构。"""
    return " ".join(str(text or "").split())


def build_system_prompt(
    base_prompt: str = SYSTEM_PROMPT,
    skills: Optional[Iterable[Tuple[str, str]]] = None,
) -> str:
    """在基础提示词后追加可用 skill 清单。

    :param base_prompt: 原有基础 System Prompt
    :param skills: ``(name, description)`` 序列；为空时原样返回基础提示词
    """
    entries: Sequence[Tuple[str, str]] = list(skills or ())
    if not entries:
        return base_prompt

    lines = ["", SKILL_PROMPT_HEADER]
    for name, description in entries:
        skill_name = str(name or "").strip()
        if not skill_name:
            continue
        lines.append(f"- '{skill_name}': {_one_line(description) or '(no description)'}")

    if len(lines) == 2:  # 只有标题，没有任何有效条目
        return base_prompt

    lines.append("")
    lines.append(SKILL_PROMPT_USAGE)
    return base_prompt.rstrip() + "\n" + "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "build_system_prompt"]
