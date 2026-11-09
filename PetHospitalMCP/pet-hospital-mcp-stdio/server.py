# -*- coding: utf-8 -*-
"""宠物医院 REST API → MCP Server（stdio 传输）。

把 Go 宠物医院服务（默认 http://127.0.0.1:8080）封装成一组**业务语义**
的 MCP 工具，供 Zagent 等 MCP 客户端通过 Function Calling 调用。

设计要点：

- **工具按业务设计，不照抄 29 个 HTTP 端点**：`pet_list` 一个工具覆盖
  按主人 / 医生 / 种类 / 疾病 / 状态 / 花费区间过滤（上游 ``GET /pets``
  本身就支持这些查询参数），避免把工具表撑爆；
- **输出裁剪**：上游列表接口会返回每只宠物完整的病历 + 收费明细，
  本服务默认只回传摘要字段（``pet_get`` 才给全量），并按字符上限截断，
  防止撑爆 LLM 上下文；
- **错误转文本**：HTTP / 业务错误统一抛 :class:`ToolError`，
  MCP 会把它标记为工具错误返回给模型，模型可据此自我纠正；
- **配置走环境变量**：``PET_API_BASE_URL`` / ``PET_API_TIMEOUT``，
  地址不硬编码（``.mcp.json`` 的 ``env`` 字段即可注入）。

兼容 MCP SDK 1.x（``FastMCP``）与 2.x（``MCPServer``）。
"""

from __future__ import annotations

import datetime as _dt
import inspect
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests

# -- MCP 错误类型 ------------------------------------------------------------
# SDK 2.x：只有 ToolError 会把原始文本回传给客户端（isError=True），
# 普通 Exception 会被吞成无信息的 "Error executing tool <name>"；
# SDK 1.x：FastMCP 把任意异常转成 isError 文本，ToolError 同样可用。
try:  # MCP SDK 2.x
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP SDK 1.x
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:  # pragma: no cover - 极老版本兜底
        class ToolError(Exception):  # type: ignore[no-redef]
            """工具错误基类兜底。"""


# -- 配置 -------------------------------------------------------------------
#: 上游 REST API 地址（可由 .mcp.json 的 env 覆盖）
BASE_URL = os.environ.get("PET_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
#: 单次 HTTP 请求超时（秒）
TIMEOUT = float(os.environ.get("PET_API_TIMEOUT", "15") or 15)
#: 单个工具返回给模型的最大字符数
MAX_CHARS = int(os.environ.get("PET_API_MAX_CHARS", "24000") or 24000)

# -- 枚举（与上游 /api/v1/meta 保持一致，用作参数 enum 便于模型选值） -------
SPECIES = ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
STATUSES = ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
GENDERS = ["公", "母"]
CHARGE_CATEGORIES = ["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"]
SORT_FIELDS = [
    "id", "name", "ownerName", "species", "doctor", "disease",
    "status", "totalCost", "visitCount", "createdAt", "updatedAt",
]

#: 列表摘要字段（病历 / 收费明细不进列表，避免超长）
_BRIEF_KEYS = (
    "id", "name", "species", "breed", "gender", "ageMonths",
    "ownerName", "ownerPhone", "doctor", "disease", "status",
    "totalCost", "visitCount", "createdAt", "updatedAt",
)


# -- HTTP 封装 ---------------------------------------------------------------
def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Any = None,
) -> Any:
    """调用上游 API，返回 ``data`` 字段；失败抛出中文 :class:`ToolError`。"""
    url = f"{BASE_URL}{path}"
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    try:
        response = requests.request(
            method,
            url,
            params=clean or None,
            json=body,
            timeout=TIMEOUT,
        )
    except requests.exceptions.Timeout as exc:
        raise ToolError(
            f"宠物医院 API 请求超时（{TIMEOUT:g} 秒）→ {url}，请确认服务已启动"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise ToolError(
            f"宠物医院 API 无法访问 → {url}（{type(exc).__name__}: {exc}）。"
            f"请确认服务已启动，或用 PET_API_BASE_URL 指定正确地址"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ToolError(
            f"API 返回非 JSON（HTTP {response.status_code}）："
            f"{response.text[:200]}"
        ) from exc

    if isinstance(payload, dict):
        code = payload.get("code", response.status_code)
        try:
            code = int(code)
        except (TypeError, ValueError):  # pragma: no cover - 防御性分支
            code = response.status_code
        if response.status_code >= 400 or code >= 400:
            message = payload.get("message") or response.text[:200]
            raise ToolError(
                f"宠物医院 API 业务错误（HTTP {response.status_code}，code={code}）：{message}"
            )
        return payload.get("data")

    if response.status_code >= 400:  # pragma: no cover - 非标准错误体
        raise ToolError(f"API 错误（HTTP {response.status_code}）")
    return payload


def _dump(data: Any) -> str:
    """序列化为紧凑可读 JSON，并按上限截断。"""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover
        text = f"（结果序列化失败：{exc}）{data!r}"
    if len(text) <= MAX_CHARS:
        return text
    return (
        f"{text[:MAX_CHARS]}\n\n"
        f"…（输出过长已截断，原长 {len(text)} 字符；"
        f"可用过滤 / 分页参数缩小范围，或用 pet_get 查看单只详情）"
    )


def _brief(pet: dict) -> dict:
    """宠物档案 -> 摘要（不含病历与收费明细）。"""
    item = {k: pet.get(k) for k in _BRIEF_KEYS if k in pet}
    if "records" in pet:
        item["recordCount"] = len(pet.get("records") or [])
    if "charges" in pet:
        item["chargeCount"] = len(pet.get("charges") or [])
    return item


def _brief_list(data: dict) -> dict:
    """列表响应 -> 摘要（保留分页元信息）。"""
    if not isinstance(data, dict):  # pragma: no cover - 防御性分支
        return data
    out = dict(data)
    items = out.get("items")
    if isinstance(items, list):
        out["items"] = [_brief(item) for item in items if isinstance(item, dict)]
        out["_hint"] = "摘要把每只宠物的病历/收费压缩为计数，完整内容用 pet_get(id) 查询"
    return out


def _today() -> str:
    return _dt.date.today().isoformat()


# -- MCP Server --------------------------------------------------------------
try:  # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer(
    "pet-hospital",
    instructions=(
        "宠物医院管理系统 MCP 服务器：提供宠物档案的查询 / 新增 / 修改 / 删除、"
        "病历追加、收费登记、费用汇总与经营统计。数据保存在上游 Go 服务的 "
        "data/pet.db 中，所有写操作都会真实生效，请在用户明确要求时才做删除类操作。"
    ),
)


def _tool(**supported_kwargs):
    """装饰器工厂：按当前 SDK 版本裁剪不支持的关键字（1.x / 2.x 差异）。"""
    try:
        params = inspect.signature(server.tool).parameters
    except (TypeError, ValueError):  # pragma: no cover
        params = {}
    kwargs = {k: v for k, v in supported_kwargs.items() if k in params}
    return server.tool(**kwargs)


@_tool()
def clinic_meta() -> str:
    """获取宠物医院的枚举字典：种类、就诊状态、性别、收费分类、可排序字段。

    在写入或过滤前先调用它，确保传入的 species / status / category 等值合法。
    """
    return _dump(_request("GET", "/api/v1/meta"))


@_tool()
def clinic_stats(top: int = 5) -> str:
    """获取医院经营统计：宠物总数、病历数、总收入、均次花费、按种类/状态/医生分布，
    以及消费排行（top）。

    :param top: 消费排行榜条数（默认 5，上限 50）
    """
    top = max(1, min(int(top or 5), 50))
    data = _request("GET", "/api/v1/stats", params={"top": top})
    if isinstance(data, dict) and isinstance(data.get("topSpenders"), list):
        data = dict(data)
        data["topSpenders"] = [
            _brief(item) for item in data["topSpenders"] if isinstance(item, dict)
        ]
    return _dump(data)


@_tool()
def pet_list(
    species: Optional[str] = None,
    status: Optional[str] = None,
    doctor: Optional[str] = None,
    owner_name: Optional[str] = None,
    owner_phone: Optional[str] = None,
    name: Optional[str] = None,
    disease: Optional[str] = None,
    min_cost: Optional[float] = None,
    max_cost: Optional[float] = None,
    sort_by: str = "createdAt",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> str:
    """按条件查询宠物档案列表（返回摘要 + 分页信息，含总数 total）。

    过滤条件可组合（全部可选，留空表示不过滤）：种类、就诊状态、主治医生、
    主人姓名 / 电话、宠物名、疾病、总花费区间。可按 sort_by 排序，
    例如按 totalCost 降序即“消费最高的一批宠物”。

    :param species: 种类（犬/猫/兔/鸟/仓鼠/爬宠/其他）
    :param status: 就诊状态（待就诊/就诊中/住院中/已康复/慢性病随访）
    :param doctor: 主治医生姓名，如 “李医生”
    :param owner_name: 主人姓名（模糊匹配）
    :param owner_phone: 主人电话（模糊匹配）
    :param name: 宠物姓名（模糊匹配）
    :param disease: 疾病 / 诊断（模糊匹配）
    :param min_cost: 总花费下限（元）
    :param max_cost: 总花费上限（元）
    :param sort_by: 排序字段，可选 id/name/ownerName/species/doctor/disease/
                    status/totalCost/visitCount/createdAt/updatedAt
    :param order: asc 升序 / desc 降序
    :param page: 页码，从 1 开始
    :param page_size: 每页条数（1~50，默认 10）
    """
    params = {
        "species": species,
        "status": status,
        "doctor": doctor,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "name": name,
        "disease": disease,
        "min": min_cost,
        "max": max_cost,
        "sortBy": sort_by or "createdAt",
        "order": order or "desc",
        "page": max(1, int(page or 1)),
        "pageSize": max(1, min(int(page_size or 10), 50)),
    }
    return _dump(_brief_list(_request("GET", "/api/v1/pets", params=params)))


@_tool()
def pet_search(query: str, page: int = 1, page_size: int = 10) -> str:
    """跨字段全文检索宠物档案（同时匹配名字、主人、医生、疾病、备注等，
    空格分词，多个词之间是“且”的关系）。

    :param query: 检索关键词，例如 “肠胃炎”、“张三 犬”、“陈医生 骨折”
    :param page: 页码，从 1 开始
    :param page_size: 每页条数（1~50）
    """
    text = (query or "").strip()
    if not text:
        raise ToolError("query 不能为空，请提供检索关键词")
    params = {
        "q": text,
        "page": max(1, int(page or 1)),
        "pageSize": max(1, min(int(page_size or 10), 50)),
    }
    return _dump(_brief_list(_request("GET", "/api/v1/pets/search", params=params)))


@_tool()
def pet_get(
    pet_id: str,
    include_records: bool = True,
    include_charges: bool = True,
) -> str:
    """按档案编号查询单只宠物的完整信息（含历史病历与收费明细）。

    :param pet_id: 档案编号，如 “PET-000001”
    :param include_records: 是否包含历史病历（默认 true）
    :param include_charges: 是否包含收费明细（默认 true；只要基本信息可传 false）
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    data = _request("GET", f"/api/v1/pets/{pet_id}")
    if isinstance(data, dict):
        if not include_records:
            data.pop("records", None)
        if not include_charges:
            data.pop("charges", None)
    return _dump(data)


@_tool()
def pet_create(
    name: str,
    species: str,
    owner_name: str,
    owner_phone: str,
    doctor: str,
    disease: str,
    status: str = "待就诊",
    breed: Optional[str] = None,
    gender: Optional[str] = None,
    age_months: Optional[int] = None,
    color: Optional[str] = None,
    chip_no: Optional[str] = None,
    owner_addr: Optional[str] = None,
    allergy: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """新建一只宠物档案，返回新档案（含自动生成的 pet_id，如 “PET-0001009”）。

    name / species / owner_name / owner_phone / doctor / disease 为必填。
    写操作会真实落库，请先与用户确认档案内容。

    :param name: 宠物姓名
    :param species: 种类（犬/猫/兔/鸟/仓鼠/爬宠/其他）
    :param owner_name: 主人姓名
    :param owner_phone: 主人电话
    :param doctor: 主治医生姓名
    :param disease: 疾病 / 主要诊断
    :param status: 就诊状态，默认 “待就诊”
                   （待就诊/就诊中/住院中/已康复/慢性病随访）
    :param breed: 品种
    :param gender: 性别（公/母）
    :param age_months: 月龄
    :param color: 毛色
    :param chip_no: 芯片号
    :param owner_addr: 主人住址
    :param allergy: 过敏史
    :param note: 备注
    """
    body = {
        "name": (name or "").strip(),
        "species": (species or "").strip(),
        "ownerName": (owner_name or "").strip(),
        "ownerPhone": (owner_phone or "").strip(),
        "doctor": (doctor or "").strip(),
        "disease": (disease or "").strip(),
        "status": (status or "待就诊").strip(),
        "breed": breed,
        "gender": gender,
        "ageMonths": age_months,
        "color": color,
        "chipNo": chip_no,
        "ownerAddr": owner_addr,
        "allergy": allergy,
        "note": note,
    }
    body = {k: v for k, v in body.items() if v not in (None, "")}
    created = _request("POST", "/api/v1/pets", body=body)
    if isinstance(created, dict):
        created = _brief(created)
        created["_hint"] = "这是新建后的摘要；用 pet_get(pet_id) 查看完整档案"
    return _dump(created)


@_tool()
def pet_update(
    pet_id: str,
    name: Optional[str] = None,
    species: Optional[str] = None,
    owner_name: Optional[str] = None,
    owner_phone: Optional[str] = None,
    doctor: Optional[str] = None,
    disease: Optional[str] = None,
    status: Optional[str] = None,
    breed: Optional[str] = None,
    gender: Optional[str] = None,
    age_months: Optional[int] = None,
    color: Optional[str] = None,
    chip_no: Optional[str] = None,
    owner_addr: Optional[str] = None,
    allergy: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """局部更新宠物档案：只传需要修改的字段，其余字段保持不变（PATCH 语义）。

    典型用法：改就诊状态 ``status="住院中"``、换主治医生 ``doctor="王医生"``。

    :param pet_id: 档案编号
    :param name: 宠物姓名
    :param species: 种类
    :param owner_name: 主人姓名
    :param owner_phone: 主人电话
    :param doctor: 主治医生
    :param disease: 疾病 / 诊断
    :param status: 就诊状态（待就诊/就诊中/住院中/已康复/慢性病随访）
    :param breed: 品种
    :param gender: 性别（公/母）
    :param age_months: 月龄
    :param color: 毛色
    :param chip_no: 芯片号
    :param owner_addr: 主人住址
    :param allergy: 过敏史
    :param note: 备注
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    mapping = {
        "name": name,
        "species": species,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "breed": breed,
        "gender": gender,
        "ageMonths": age_months,
        "color": color,
        "chipNo": chip_no,
        "ownerAddr": owner_addr,
        "allergy": allergy,
        "note": note,
    }
    body = {k: v for k, v in mapping.items() if v is not None}
    if not body:
        raise ToolError("未提供任何要更新的字段，请至少传一个（如 status）")
    updated = _request("PATCH", f"/api/v1/pets/{pet_id}", body=body)
    return _dump(_brief(updated) if isinstance(updated, dict) else updated)


@_tool()
def pet_delete(pet_id: str) -> str:
    """删除一只宠物档案（同时删除其病历与收费明细）。

    ⚠️ 不可恢复的破坏性操作：仅在用户**明确要求删除**时才调用，
    且调用前应复述宠物信息（名字 / 主人 / 编号）向用户二次确认。

    :param pet_id: 档案编号
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    data = _request("DELETE", f"/api/v1/pets/{pet_id}")
    return _dump({"deleted": pet_id, "result": data})


@_tool()
def pet_record_add(
    pet_id: str,
    doctor: str,
    diagnosis: str,
    visit_date: Optional[str] = None,
    symptoms: Optional[str] = None,
    treatment: Optional[str] = None,
    prescription: Optional[List[str]] = None,
    weight_kg: Optional[float] = None,
    temperature: Optional[float] = None,
    follow_up: Optional[str] = None,
    charge: Optional[float] = None,
) -> str:
    """为宠物追加一条就诊病历（写操作，会真实落库）。

    返回追加后的摘要：档案编号、就诊次数、本次病历、最新总花费。
    注意：**本工具只写病历，不会自动产生收费**；如需记账请再调 pet_charge_add。

    :param pet_id: 档案编号
    :param doctor: 接诊医生
    :param diagnosis: 诊断结论（必填）
    :param visit_date: 就诊日期 YYYY-MM-DD（默认今天）
    :param symptoms: 症状描述
    :param treatment: 治疗方案
    :param prescription: 用药列表，如 ["阿莫西林", "蒙脱石散"]
    :param weight_kg: 体重（kg）
    :param temperature: 体温（℃）
    :param follow_up: 复诊建议
    :param charge: 本次就诊花费金额（元）——注意这是病历字段，不等于收费明细
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    if not (diagnosis or "").strip():
        raise ToolError("diagnosis(诊断) 不能为空")
    body = {
        "visitDate": (visit_date or "").strip() or _today(),
        "doctor": (doctor or "").strip(),
        "diagnosis": diagnosis.strip(),
        "symptoms": symptoms,
        "treatment": treatment,
        "prescription": prescription,
        "weightKg": weight_kg,
        "temperature": temperature,
        "followUp": follow_up,
        "charge": charge,
    }
    body = {k: v for k, v in body.items() if v is not None}
    pet = _request("POST", f"/api/v1/pets/{pet_id}/records", body=body)
    return _dump(_record_result(pet, body))


def _record_result(pet: Any, sent: dict) -> Any:
    """病历接口返回整只宠物，这里裁成“本次病历 + 汇总”。"""
    if not isinstance(pet, dict):  # pragma: no cover
        return pet
    records = pet.get("records") or []
    last = records[-1] if records else sent
    return {
        "petId": pet.get("id"),
        "petName": pet.get("name"),
        "ownerName": pet.get("ownerName"),
        "status": pet.get("status"),
        "visitCount": pet.get("visitCount"),
        "totalCost": pet.get("totalCost"),
        "addedRecord": last,
        "_hint": "如需登记本次收费，请调用 pet_charge_add",
    }


@_tool()
def pet_charge_add(
    pet_id: str,
    item: str,
    category: str,
    amount: float,
    doctor: Optional[str] = None,
    date: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """为宠物登记一笔收费（写操作，会真实落库，并自动累计总花费）。

    返回新增的收费条目与该宠物的最新累计花费。

    :param pet_id: 档案编号
    :param item: 收费项目，如 “血常规检查”
    :param category: 收费分类（检查/药品/手术/住院/疫苗/护理/其他）
    :param amount: 金额（元，正数）
    :param doctor: 收费医生
    :param date: 收费日期 YYYY-MM-DD（默认今天）
    :param note: 备注
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    if not (item or "").strip():
        raise ToolError("item(收费项目) 不能为空")
    try:
        amount_value = float(amount)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"amount(金额) 必须是数字 -> {amount!r}") from exc
    if amount_value <= 0:
        raise ToolError(f"amount(金额) 必须大于 0 -> {amount!r}")
    body = {
        "item": item.strip(),
        "category": (category or "").strip() or "其他",
        "amount": amount_value,
        "doctor": (doctor or "").strip(),
        "date": (date or "").strip() or _today(),
        "note": note,
    }
    body = {k: v for k, v in body.items() if v not in (None, "")}
    pet = _request("POST", f"/api/v1/pets/{pet_id}/charges", body=body)
    if not isinstance(pet, dict):  # pragma: no cover
        return _dump(pet)
    charges = pet.get("charges") or []
    last = charges[-1] if charges else body
    by_category: Dict[str, float] = {}
    for charge in charges:
        key = str(charge.get("category") or "其他")
        by_category[key] = round(by_category.get(key, 0) + float(charge.get("amount") or 0), 2)
    return _dump(
        {
            "petId": pet.get("id"),
            "petName": pet.get("name"),
            "addedCharge": last,
            "totalCost": pet.get("totalCost"),
            "chargeCount": len(charges),
            "costByCategory": by_category,
        }
    )


@_tool()
def pet_summary(pet_id: str) -> str:
    """查询单只宠物的费用与就诊汇总：总花费、就诊次数、次均花费、
    按分类 / 医生的花费构成、首末次就诊日期、病史时间线。

    适合回答“这只宠物一共花了多少钱”“治疗过几次”这类问题。

    :param pet_id: 档案编号
    """
    pet_id = (pet_id or "").strip()
    if not pet_id:
        raise ToolError("pet_id 不能为空，例如 PET-000001")
    return _dump(_request("GET", f"/api/v1/pets/{pet_id}/summary"))


@_tool()
def pet_ranking(limit: int = 10) -> str:
    """消费排行榜：按在医院总花费从高到低列出前 N 只宠物（摘要）。

    :param limit: 返回条数（1~50，默认 10）
    """
    limit = max(1, min(int(limit or 10), 50))
    data = _request("GET", "/api/v1/pets/top-spenders", params={"limit": limit})
    if isinstance(data, list):
        return _dump([_brief(item) for item in data if isinstance(item, dict)])
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = dict(data)
        data["items"] = [_brief(i) for i in data["items"] if isinstance(i, dict)]
    return _dump(data)


def main() -> int:
    # stdout 保留给 MCP JSON-RPC；日志一律走 stderr
    import logging

    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("PET_MCP_LOG_LEVEL", "WARNING").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
