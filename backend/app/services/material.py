"""养护材料业务规则：状态流转、批量领用与库存口径都收在这里。

设计要点：
- 批量领用按条独立处理：任意一条校验不通过都不影响其余条目，没有整体回退。
- 冻结/耗尽材料不能再领用，已耗尽的材料在批量冻结时自动跳过。
- 结存掉到储备下限以下自动转为「临近不足」，归零则「已耗尽」。
- 同一批养护材料凭批次号幂等：重复提交只扣一次库存、只留一次处理结果。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "material"
REQUIRED_FIELDS = ["材料编号", "材料名称", "规格型号"]
STATUS_ORDER = ["正常可用", "临近不足", "已冻结", "已耗尽"]
ACTION_RULES = {"冻结材料": "已冻结", "解冻材料": "正常可用", "登记耗尽": "已耗尽"}
NEGATIVE_ACTIONS = []

# 默认储备下限：种子数据里未显式给出时按结存的比例兜底。
DEFAULT_LOWER_RATIO = 0.3
DEFAULT_LOWER_LIMIT = 5

# 允许参与领用的状态；冻结、耗尽材料一律拦下。
CONSUMABLE_STATUSES = {"正常可用", "临近不足"}


class MaterialService:
    def __init__(self) -> None:
        # 批次号 -> 批量处理结果，保证重复提交幂等
        self._batch_results: dict[str, dict[str, Any]] = {}

    # ---------- 列表与登记 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("材料编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["结存数量"] = _to_int(values.get("结存数量"), default=0)
        entry["储备下限"] = self._lower_limit(
            values.get("储备下限"), entry["结存数量"]
        )
        entry["计量单位"] = values.get("计量单位") or ""
        entry["存放场地"] = values.get("存放场地") or ""
        entry["保管人员"] = values.get("保管人员") or ""
        entry["status"] = self._derive_status(entry["结存数量"], entry["储备下限"])
        entry["材料状态"] = entry["status"]
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        entry["abnormal"] = entry["status"] == "临近不足"
        rows.append(entry)
        return entry, []

    # ---------- 单条动作 ----------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"养护材料 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于养护材料可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "冻结材料" and entry.get("status") == "已耗尽":
            # 已耗尽材料冻结没有意义，自动跳过而不是报错
            return entry, "材料已耗尽，冻结时自动跳过"
        if action == "登记耗尽":
            # 登记耗尽即库存清零，和领用到归零走同一口径
            entry["结存数量"] = 0
            entry["status"] = target
            entry["abnormal"] = True
        elif action == "解冻材料":
            # 解冻后按库存恢复真实状态：低于下限的回到临近不足
            balance = _to_int(entry.get("结存数量"), default=0)
            lower_limit = self._lower_limit(entry.get("储备下限"), balance)
            entry["储备下限"] = lower_limit
            entry["status"] = self._derive_status(balance, lower_limit)
            entry["abnormal"] = entry["status"] == "临近不足"
        else:
            entry["status"] = target
            entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["材料状态"] = entry["status"]
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        return entry, f"养护材料已{action}"

    # ---------- 批量领用 ----------

    def consume_batch(
        self, items: list[dict[str, Any]], batch_no: str | None
    ) -> tuple[dict[str, Any] | None, str]:
        """勾选多条养护材料后一次提交领用。

        返回 (结果, 错误说明)：入参整体不合法时错误说明非空；
        条目级失败写进 results，互不影响、不回退。
        """
        batch_no = str(batch_no or "").strip()
        if not batch_no:
            return None, "缺少批次号，无法保证重复提交只扣一次"
        if not items:
            return None, "请至少勾选一条养护材料再提交"

        # 幂等：同一批次号重复提交，直接回放首次结果，库存与记录都不再动
        existed = self._batch_results.get(batch_no)
        if existed is not None:
            replay = dict(existed)
            replay["replayed"] = True
            return replay, ""

        # 同批内重复勾选同一条材料：只认第一次出现，避免一批扣多遍
        seen: set[int] = set()
        results: list[dict[str, Any]] = []
        for item in items:
            entry_id = _to_int(item.get("id"), default=None)
            quantity = _to_int(item.get("领用数量"), default=None)
            row_key = f"{item.get('材料编号', '')}#{item.get('id', '')}"

            if entry_id is None or entry_id in seen:
                dup_entry = store.find(MODULE, entry_id) if entry_id is not None else None
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": (dup_entry or {}).get("材料编号", item.get("材料编号", "")),
                    "ok": False,
                    "reason": "同一批次内重复提交，只处理一次",
                })
                if entry_id is not None:
                    seen.add(entry_id)
                continue
            seen.add(entry_id)

            entry = store.find(MODULE, entry_id)
            if entry is None:
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": item.get("材料编号", ""),
                    "ok": False,
                    "reason": "养护材料不存在或已归档",
                })
                continue
            if quantity is None or quantity <= 0:
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": entry.get("材料编号", ""),
                    "ok": False,
                    "reason": "领用数量必须是大于 0 的整数",
                })
                continue

            status = str(entry.get("status") or "")
            if status == "已耗尽":
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": entry.get("材料编号", ""),
                    "ok": False,
                    "reason": "材料已耗尽，自动跳过",
                })
                continue
            if status == "已冻结":
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": entry.get("材料编号", ""),
                    "ok": False,
                    "reason": "材料已冻结，不能领用",
                })
                continue

            balance = _to_int(entry.get("结存数量"), default=0)
            if quantity > balance:
                results.append({
                    "key": row_key,
                    "id": entry_id,
                    "材料编号": entry.get("材料编号", ""),
                    "ok": False,
                    "reason": f"领用数量超过可用数量（可用 {balance}）",
                    "before": balance,
                    "after": balance,
                })
                continue

            # 校验通过才真正扣减；每条独立提交，失败条目不影响已扣减的条目
            lower_limit = self._lower_limit(entry.get("储备下限"), balance)
            entry["储备下限"] = lower_limit
            after = balance - quantity
            entry["结存数量"] = after
            new_status = self._derive_status(after, lower_limit)
            entry["status"] = new_status
            entry["材料状态"] = new_status
            entry["pending"] = new_status != "已耗尽"
            entry["abnormal"] = new_status == "临近不足"
            results.append({
                "key": row_key,
                "id": entry_id,
                "材料编号": entry.get("材料编号", ""),
                "材料名称": entry.get("材料名称", ""),
                "ok": True,
                "reason": "领用成功",
                "consumed": quantity,
                "before": balance,
                "after": after,
                "status": new_status,
                "dropped_low": new_status == "临近不足"
                and status not in {"临近不足"},
            })

        summary = {
            "batch_no": batch_no,
            "replayed": False,
            "total": len(results),
            "succeeded": sum(1 for row in results if row["ok"]),
            "failed": sum(1 for row in results if not row["ok"]),
            "results": results,
        }
        # 只留一次处理结果：后续同批次号提交全部回放这份记录
        self._batch_results[batch_no] = summary
        return dict(summary), ""

    def list_batch_records(self) -> list[dict[str, Any]]:
        """历次批量领用处理结果，按提交顺序倒序返回。"""
        return [dict(record) for record in reversed(list(self._batch_results.values()))]

    # ---------- 库存看板 ----------

    def inventory_board(self) -> dict[str, Any]:
        """库存看板口径：可用数量就是可参与领用的结存，与领用界面逐条一致。"""
        rows = store.rows(MODULE)
        items: list[dict[str, Any]] = []
        total_balance = 0
        total_available = 0
        for row in rows:
            balance = _to_int(row.get("结存数量"), default=0)
            lower_limit = self._lower_limit(row.get("储备下限"), balance)
            row.setdefault("储备下限", lower_limit)
            status = str(row.get("status") or self._derive_status(balance, lower_limit))
            available = balance if status in CONSUMABLE_STATUSES else 0
            total_balance += balance
            total_available += available
            items.append({
                "id": row.get("id"),
                "材料编号": row.get("材料编号", ""),
                "材料名称": row.get("材料名称", ""),
                "规格型号": row.get("规格型号", ""),
                "计量单位": row.get("计量单位", ""),
                "结存数量": balance,
                "可用数量": available,
                "储备下限": lower_limit,
                "status": status,
            })
        return {
            "items": items,
            "cards": [
                {"label": "材料品类", "value": len(items)},
                {"label": "结存总量", "value": total_balance},
                {"label": "可用总量", "value": total_available},
                {"label": "临近不足", "value": sum(1 for item in items if item["status"] == "临近不足")},
                {"label": "已冻结/耗尽", "value": sum(1 for item in items if item["status"] in {"已冻结", "已耗尽"})},
            ],
        }

    # ---------- 内部口径 ----------

    @staticmethod
    def _lower_limit(raw: Any, balance: int) -> int:
        limit = _to_int(raw, default=None)
        if limit is not None and limit >= 0:
            return limit
        # 种子数据没有储备下限：取结存与默认下限的比例，至少 1，保证归零能被识别
        return max(1, min(balance, DEFAULT_LOWER_LIMIT, int(balance * DEFAULT_LOWER_RATIO) or 1))

    @staticmethod
    def _derive_status(balance: int, lower_limit: int) -> str:
        if balance <= 0:
            return "已耗尽"
        if balance < lower_limit:
            return "临近不足"
        return "正常可用"


def _to_int(raw: Any, *, default: int | None = None) -> int | None:
    """把前端/种子里可能的字符串数字转成整数；非法值回落到 default。"""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    try:
        text = str(raw).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default
