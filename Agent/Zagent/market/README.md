# Zagent 本地技能市场

把技能包放到本目录下即可出现在 Zagent 的技能市场中：

```text
market/
└── <包目录>/
    ├── skill.md          # 必须，YAML Frontmatter + 技能正文
    └── references/...    # 可选，技能引用的附加文件
```

`skill.md` 示例：

```markdown
---
name: pdf-tools
description: 从 PDF 中提取文本与表格
version: 1.0.0
author: your-name
category: 文档
tags: [pdf, extract]
homepage: https://example.com
---

# PDF 工具

具体操作步骤……需要接口细节时读取 `references/api.md`。
```

必填字段只有 `name` 和 `description`；`version`、`author`、`category`、
`tags`、`homepage` 用于市场展示。`name` 会作为安装后的目录名，
因此不能包含路径分隔符或系统保留字符。

## 使用

- TUI：按 `F2`，或输入 `/market`
- CLI：输入 `/market` 进入交互式市场，或直接：

```text
/market list
/market search pdf
/market info pdf-tools
/market install pdf-tools
/market install pdf-tools --force
/market uninstall pdf-tools
```

安装目标为项目根目录下的 `.agents/skills/<name>/`；安装 / 卸载后会立即
刷新 Agent 的 skill 清单和 System Prompt。`.agents/skills/` 中已有、但
不在市场源目录中的技能也会以“本地已安装”形式显示，可查看详情和卸载。

市场目录可通过 `[agent] market_dir` 或环境变量 `ZAGENT_MARKET_DIR` 修改。
