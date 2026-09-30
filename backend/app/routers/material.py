"""养护材料接口：维护养护材料，覆盖勾选多条批量领用/冻结/解冻/耗尽与库存看板。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchActionPayload,
    BatchActionResult,
    EntryPayload,
    InventoryBoard,
    PageResult,
)
from app.services.material import MaterialService

router = APIRouter(prefix="/api/material", tags=["养护材料"])

service = MaterialService()

LIST_FIELDS = ["材料编号", "材料名称", "规格型号", "结存数量", "储备下限", "可用数量", "计量单位", "存放场地", "保管人员", "材料状态"]
STATUSES = ["正常可用", "临近不足", "已冻结", "已耗尽"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按材料编号检索"),
    status: str | None = Query(default=None, description="正常可用、临近不足、已冻结、已耗尽"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按材料编号与状态过滤养护材料列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/inventory", response_model=InventoryBoard)
def inventory_board() -> InventoryBoard:
    """库存看板：结存数量、可用数量与储备不足口径在这里统一计算，领用界面同源展示。"""
    data = service.inventory()
    return InventoryBoard.model_validate(data)


@router.get("/batches/{batch_no}", response_model=BatchActionResult)
def get_batch(batch_no: str) -> BatchActionResult:
    """按批次号回查首次批量处理结果；用于页面刷新后核对，不会再次扣减库存。"""
    result = service.find_batch(batch_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 未找到，可能尚未提交或服务已重启")
    return BatchActionResult.model_validate(result)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条养护材料明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"养护材料 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条养护材料，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="养护材料已登记", entry=entry)


@router.post("/batch-actions", response_model=BatchActionResult)
def run_batch(payload: BatchActionPayload) -> BatchActionResult:
    """勾选多条养护材料后一次提交。

    逐条独立处理：耗尽材料自动跳过、不合法条目记失败；任意一条不通过都不影响
    其余条目生效，成功条目不回退。同一批材料重复提交只回放首次结果，只扣一次库存。
    """
    try:
        result = service.batch_run(
            action=payload.action,
            items=[(item.id, item.quantity) for item in payload.items],
            batch_no=payload.batch_no,
        )
    except ValueError as exc:
        # 请求级别错误（空勾选、未知动作、重复 id、批次号撞批）在整批处理前拦下。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BatchActionResult.model_validate(result)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条养护材料执行冻结材料、解冻材料、登记耗尽；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, outcome = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    # 自动跳过属于按规则未改动，不算操作失败；只有 failed 才给 ok=False。
    ok = outcome != "failed"
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出养护材料清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "material", "total": total, "items": items}
