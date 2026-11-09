# -*- coding: utf-8 -*-
"""Agent Skills 层：发现、注册与按需加载。

- :class:`Skill`        单个 skill 的元信息（name / description / 目录）
- :class:`SkillCatalog` 初始化时扫描到的全部 skill
- :func:`discover_skills` 扫描 ``<项目根目录>/.agents/skills/*/skill.md``

初始化阶段只读取 YAML Frontmatter 中的 ``name`` / ``description``；
完整正文由 ``loadSkill`` 工具在模型需要时读取，新技能由 ``createSKill`` 创建。
"""

from .catalog import (
    SKILL_ENTRY_NAME,
    SKILLS_DIRNAME,
    SKILLS_SUBDIR,
    Skill,
    SkillCatalog,
    discover_skills,
    find_skill_entry,
    parse_frontmatter,
    read_skill_frontmatter,
)

__all__ = [
    "SKILL_ENTRY_NAME",
    "SKILLS_DIRNAME",
    "SKILLS_SUBDIR",
    "Skill",
    "SkillCatalog",
    "discover_skills",
    "find_skill_entry",
    "parse_frontmatter",
    "read_skill_frontmatter",
]
