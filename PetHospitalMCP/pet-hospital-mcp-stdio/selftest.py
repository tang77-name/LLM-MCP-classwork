# -*- coding: utf-8 -*-
"""宠物医院 MCP Server 端到端自测。

通过 MCP SDK 的 stdio 客户端启动 ``server.py``，对工具做真实调用，
覆盖链路：工具发现 → 统计 → 列表过滤 → 检索 → 新增 → 病历 → 收费 →
汇总 → 局部更新 → 排行 → 错误路径 → 删除清理。

前置条件：宠物医院 Go 服务已启动（默认 http://127.0.0.1:8080）。

用法::

    python selftest.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = os.path.join(HERE, "server.py")

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


def is_error(result) -> bool:
    """工具结果是否为错误（兼容 SDK 1.x ``isError`` / 2.x ``is_error``）。"""
    for attr in ("isError", "is_error"):
        value = getattr(result, attr, None)
        if value is not None and not callable(value):
            return bool(value)
    # dict 形态兜底
    if isinstance(result, dict):
        return bool(result.get("isError") or result.get("is_error"))
    return False


def text_of(result) -> str:
    """提取工具结果的文本内容。"""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def as_json(result) -> dict:
    """工具结果文本 -> JSON（解析失败返回 {} 并记录）。"""
    raw = text_of(result)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"_list": data}
    except ValueError:
        return {"_raw": raw}


async def run() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER],
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PET_API_BASE_URL": os.environ.get(
                "PET_API_BASE_URL", "http://127.0.0.1:8080"
            ),
        },
    )
    tag = "SELFTEST-" + uuid.uuid4().hex[:8]
    created_id = None

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1) 工具发现
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            expected = {
                "clinic_meta", "clinic_stats", "pet_list", "pet_search",
                "pet_get", "pet_create", "pet_update", "pet_delete",
                "pet_record_add", "pet_charge_add", "pet_summary", "pet_ranking",
            }
            check(
                "工具发现 12 个且命名正确",
                expected.issubset(set(names)),
                f"missing={sorted(expected - set(names))} actual={names}",
            )
            meta_tool = next((t for t in tools.tools if t.name == "clinic_meta"), None)
            schema = getattr(meta_tool, "inputSchema", None) or getattr(
                meta_tool, "input_schema", None
            )
            check(
                "工具带描述与参数 Schema",
                bool(meta_tool and meta_tool.description and schema),
            )

            # 2) 统计
            stats = as_json(await session.call_tool("clinic_stats", {"top": 3}))
            check("clinic_stats 返回总数", isinstance(stats.get("totalPets"), int), str(stats)[:200])

            # 3) 枚举字典
            meta = as_json(await session.call_tool("clinic_meta", {}))
            check("clinic_meta 含 species 枚举", "犬" in (meta.get("species") or []), str(meta)[:200])

            # 4) 新增
            created = as_json(
                await session.call_tool(
                    "pet_create",
                    {
                        "name": tag,
                        "species": "猫",
                        "owner_name": "自测主人",
                        "owner_phone": "13900001111",
                        "doctor": "李医生",
                        "disease": "自测疾病",
                        "status": "待就诊",
                        "breed": "自测品种",
                    },
                )
            )
            created_id = created.get("id")
            check("pet_create 返回新档案编号", bool(created_id and created_id.startswith("PET-")), str(created)[:200])
            check("pet_create 摘要不含病历明细", "records" not in created, str(created)[:200])

            # 5) 列表过滤
            lst = as_json(
                await session.call_tool(
                    "pet_list", {"name": tag, "page_size": 5, "sort_by": "createdAt"}
                )
            )
            items = lst.get("items") or []
            check(
                "pet_list 按姓名过滤命中",
                any(i.get("id") == created_id for i in items),
                str(lst)[:300],
            )
            check("pet_list 条目为摘要", all("records" not in i for i in items))

            # 6) 检索
            found = as_json(await session.call_tool("pet_search", {"query": tag}))
            check(
                "pet_search 命中新建档案",
                any(i.get("id") == created_id for i in (found.get("items") or [])),
                str(found)[:300],
            )

            # 7) 详情
            detail = as_json(
                await session.call_tool("pet_get", {"pet_id": created_id})
            )
            check("pet_get 返回完整档案", detail.get("id") == created_id, str(detail)[:200])

            # 8) 追加病历
            rec = as_json(
                await session.call_tool(
                    "pet_record_add",
                    {
                        "pet_id": created_id,
                        "doctor": "李医生",
                        "diagnosis": "自测诊断",
                        "symptoms": "精神尚可",
                        "treatment": "观察",
                        "prescription": ["维生素"],
                        "weight_kg": 3.5,
                        "temperature": 38.1,
                    },
                )
            )
            check("pet_record_add 就诊次数=1", rec.get("visitCount") == 1, str(rec)[:300])
            check(
                "pet_record_add 返回本次病历",
                (rec.get("addedRecord") or {}).get("diagnosis") == "自测诊断",
                str(rec)[:300],
            )

            # 9) 登记收费
            charge = as_json(
                await session.call_tool(
                    "pet_charge_add",
                    {
                        "pet_id": created_id,
                        "item": "血常规检查",
                        "category": "检查",
                        "amount": 120,
                        "doctor": "李医生",
                    },
                )
            )
            check("pet_charge_add 金额入账", charge.get("totalCost") == 120, str(charge)[:300])
            check(
                "pet_charge_add 含分类汇总",
                (charge.get("costByCategory") or {}).get("检查") == 120,
                str(charge)[:300],
            )

            # 10) 费用汇总
            summary = as_json(await session.call_tool("pet_summary", {"pet_id": created_id}))
            check("pet_summary 总花费=120", summary.get("totalCost") == 120, str(summary)[:300])
            check("pet_summary 就诊次数=1", summary.get("visitCount") == 1, str(summary)[:300])

            # 11) 局部更新
            updated = as_json(
                await session.call_tool("pet_update", {"pet_id": created_id, "status": "就诊中"})
            )
            check("pet_update 改状态生效", updated.get("status") == "就诊中", str(updated)[:200])
            check(
                "pet_update 其余字段未被清空",
                updated.get("name") == tag,
                str(updated)[:200],
            )

            # 12) 排行榜
            rank = await session.call_tool("pet_ranking", {"limit": 5})
            rank_raw = text_of(rank)
            rank_data = as_json(rank)
            check(
                "pet_ranking 返回排行",
                isinstance(rank_data.get("items"), list) and len(rank_data["items"]) > 0,
                rank_raw[:200],
            )

            # 13) 错误路径：不存在的档案
            err = await session.call_tool("pet_get", {"pet_id": "PET-999999"})
            check(
                "错误路径返回错误标记",
                is_error(err),
                text_of(err)[:200],
            )
            check(
                "错误信息为中文可读文本",
                "错误" in text_of(err) or "HTTP" in text_of(err),
                text_of(err)[:200],
            )

            # 14) 参数校验错误
            err2 = await session.call_tool("pet_charge_add", {"pet_id": created_id, "item": "x", "category": "检查", "amount": -5})
            check("金额为负时拒绝", is_error(err2), text_of(err2)[:200])

            # 15) 删除清理
            deleted = as_json(await session.call_tool("pet_delete", {"pet_id": created_id}))
            check("pet_delete 成功", deleted.get("deleted") == created_id, str(deleted)[:200])
            after = await session.call_tool("pet_get", {"pet_id": created_id})
            check("删除后查不到", is_error(after), text_of(after)[:200])

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
