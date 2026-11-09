# Pet Hospital MCP Server（stdio）

把宠物医院 Go REST API（默认 `http://127.0.0.1:8080`）封装成 **12 个业务语义的
MCP 工具**，供 Zagent 等 MCP 客户端通过 Function Calling 调用。

与教学项目 `pet-hospital-mcp`（Streamable HTTP、仅 `list_pets` 的 Stage 1
脚手架）**相互独立**：本目录是可直接被 Zagent `.mcp.json` 拉起的 stdio 服务器，
不依赖、不修改教学项目。

## 依赖

- Python 3.11+
- `mcp`（1.x 或 2.x 均可，已在 2.3.0 验证）
- `requests`

```powershell
pip install "mcp>=1.9" requests
```

## 启动

```powershell
# 先启动上游 Go 服务（在其目录下）
.\pet-hospital-windows-amd64\windows\pethospital.exe

# 单独启动本 MCP Server（stdio，通常由客户端自动拉起）
python E:\LLM\PetHospitalMCP\pet-hospital-mcp-stdio\server.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PET_API_BASE_URL` | `http://127.0.0.1:8080` | 上游 REST API 地址 |
| `PET_API_TIMEOUT` | `15` | 单次 HTTP 超时（秒） |
| `PET_API_MAX_CHARS` | `24000` | 单个工具返回给模型的最大字符数 |
| `PET_MCP_LOG_LEVEL` | `WARNING` | stderr 日志级别 |

## 接入 Zagent

在 Zagent 项目根目录 `.mcp.json` 的 `mcpServers` 中加入：

```json
"PetHospital": {
  "type": "stdio",
  "command": "python",
  "args": ["E:/LLM/PetHospitalMCP/pet-hospital-mcp-stdio/server.py"],
  "cwd": "E:/LLM/PetHospitalMCP/pet-hospital-mcp-stdio",
  "env": { "PET_API_BASE_URL": "http://127.0.0.1:8080" },
  "timeout": 30
}
```

## 工具清单（12 个）

| 工具 | 用途 |
|---|---|
| `clinic_meta` | 枚举字典（种类/状态/性别/收费分类/可排序字段），写入过滤前先调用 |
| `clinic_stats` | 经营统计：总数、总收入、均次花费、分布、消费排行 |
| `pet_list` | 组合过滤查询（种类/状态/医生/主人/疾病/花费区间 + 排序分页），返回摘要 |
| `pet_search` | 跨字段全文检索（空格分词，多词为“且”） |
| `pet_get` | 单只完整档案（可选不含病历/收费明细） |
| `pet_create` | 新建档案，返回自动生成的 `PET-xxxxxx` 编号 |
| `pet_update` | 局部更新（PATCH 语义，只改传入字段） |
| `pet_delete` | ⚠️ 删除档案（不可恢复，描述中要求模型二次确认） |
| `pet_record_add` | 追加就诊病历（只写病历，不产生收费） |
| `pet_charge_add` | 登记收费，自动累计总花费并给出分类汇总 |
| `pet_summary` | 单只费用与就诊汇总（总花费、次均、分类/医生构成、病史时间线） |
| `pet_ranking` | 消费排行榜（按总花费降序） |

### 设计要点

- **按业务封装，不照抄 29 个 HTTP 端点**：`pet_list` 覆盖了 `by-owner` /
  `by-doctor` / `by-species` / `by-status` / `by-disease` / `cost-range`
  六个专用过滤端点的能力（上游 `GET /pets` 本身就支持这些查询参数）。
- **输出裁剪**：上游列表接口会返回每只宠物完整的病历 + 收费明细，
  本服务默认回传摘要（病历/收费压缩为计数），`pet_get` 才给全量，
  并按 `PET_API_MAX_CHARS` 截断，防止撑爆 LLM 上下文。
- **错误即文本**：HTTP / 业务错误抛 `ToolError`（SDK 2.x 只有它能回传
  原文），模型能看到 `错误：…` 并自我纠正；API 未启动时提示确认地址。
- **stdout 纯净**：MCP JSON-RPC 走 stdout，日志一律 stderr。

## 自测

```powershell
$env:PYTHONIOENCODING='utf-8'
python E:\LLM\PetHospitalMCP\pet-hospital-mcp-stdio\selftest.py
```

24 项断言，覆盖：工具发现 → 统计 → 枚举 → 新增 → 列表过滤 → 检索 →
详情 → 病历 → 收费 → 汇总 → 局部更新 → 排行 → 错误路径 → 参数校验 →
删除清理（自建数据自删，不污染库）。

## 端到端（Zagent + 真实 LLM）

```powershell
cd E:\LLM\Agent\Zagent
$env:PYTHONIOENCODING='utf-8'
python tools_mcp_test.py          # 18/18，含 Bazi + PetHospital 双 server
python llm_mcp_e2e.py "帮我新建一只宠物档案：名字豆包，种类猫……"
```
