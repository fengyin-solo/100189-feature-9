"""养护材料业务规则：储备下限、状态流转、批量领用与冻结的逐条处理口径都收在这里。

关键约定：
- 结存数量掉到储备下限以下时，材料自动落入「临近不足」并照常参与领用；
- 可用数量 = 结存数量，但「已冻结 / 已耗尽」材料对外可用数量为 0；
- 批量处理逐条独立生效：任意一条不通过，其余条目照常扣减且不会回退；
- 同一批材料重复提交（相同 batch_no 或相同动作+条目+数量指纹）只扣一次库存、只留一次结果。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.store import store

MODULE = "material"
REQUIRED_FIELDS = ["材料编号", "材料名称", "规格型号"]
# 新建材料未指定储备下限时的默认值。
DEFAULT_RESERVE_FLOOR = 10

STATUS_NORMAL = "正常可用"
STATUS_LOW = "临近不足"
STATUS_FROZEN = "已冻结"
STATUS_EMPTY = "已耗尽"
STATUS_ORDER = [STATUS_NORMAL, STATUS_LOW, STATUS_FROZEN, STATUS_EMPTY]

# 单条动作沿用旧入口；批量动作额外支持「领用出库」。
SINGLE_ACTIONS = ["冻结材料", "解冻材料", "登记耗尽"]
BATCH_ACTIONS = ["领用出库", "冻结材料", "解冻材料", "登记耗尽"]
ACTION_TARGET_HINT = {
    "冻结材料": "冻结",
    "解冻材料": "解冻",
    "登记耗尽": "登记耗尽",
    "领用出库": "领用出库",
}

# 三种逐条处理结论。
OUTCOME_SUCCESS = "success"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"


def _to_int(value: Any) -> int | None:
    """把结存/领用数量转成整数；空值与无法解析的值返回 None，交由调用方决定口径。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MaterialService:
    def __init__(self) -> None:
        # 幂等记录：按调用方批次号、按内容指纹各建一个索引。
        self._batches_by_key: dict[str, dict[str, Any]] = {}
        self._fingerprint_keys: dict[str, str] = {}
        # 服务启动时校准一次存量材料，让运营看板的待处理/异常量与材料口径一致。
        for row in store.rows(MODULE):
            self._refresh(row)

    # ------------------------------------------------------------------ 读取

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._refresh(dict(row)) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("材料编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._refresh(row) if row is not None else None

    def inventory(self) -> dict[str, Any]:
        """库存看板数据：领用界面和运营看板都取这一份，结存与可用口径天然一致。"""
        rows = [self._refresh(dict(row)) for row in store.rows(MODULE)]
        cards = [
            {"label": "材料种类", "value": len(rows)},
            {"label": "可领用材料", "value": sum(1 for row in rows if int(row["可用数量"]) > 0)},
            {"label": "结存总量合计", "value": sum(int(row["结存数量"]) for row in rows)},
            {"label": "可用总量合计", "value": sum(int(row["可用数量"]) for row in rows)},
            {"label": "临近不足", "value": sum(1 for row in rows if row["status"] == STATUS_LOW)},
            {"label": "已冻结", "value": sum(1 for row in rows if row["status"] == STATUS_FROZEN)},
            {"label": "已耗尽", "value": sum(1 for row in rows if row["status"] == STATUS_EMPTY)},
        ]
        return {"cards": cards, "items": rows}

    # ------------------------------------------------------------------ 写入

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["结存数量"] = _to_int(values.get("结存数量")) or 0
        entry["储备下限"] = _to_int(values.get("储备下限"))
        if entry["储备下限"] is None or entry["储备下限"] < 0:
            entry["储备下限"] = DEFAULT_RESERVE_FLOOR
        entry["计量单位"] = values.get("计量单位") or "—"
        entry["存放场地"] = values.get("存放场地") or "—"
        entry["保管人员"] = values.get("保管人员") or "—"
        rows.append(entry)
        return self._refresh(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, str]:
        """单条动作：返回 (材料, 说明, 结论)；材料不存在时材料为 None。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"养护材料 {entry_id} 不存在或已归档", OUTCOME_FAILED
        if action not in SINGLE_ACTIONS:
            return None, f"动作「{action}」不属于养护材料可执行范围", OUTCOME_FAILED
        result = self._process(entry, action, None)
        self._refresh(entry)
        return entry, result["reason"], result["outcome"]

    def find_batch(self, batch_no: str) -> dict[str, Any] | None:
        """按批次号回查首次处理结果。"""
        record = self._batches_by_key.get(batch_no.strip())
        return record["result"] if record else None

    def batch_run(
        self,
        action: str,
        items: list[tuple[int, int | None]],
        batch_no: str | None = None,
    ) -> dict[str, Any]:
        """批量处理多条材料；逐条独立生效，整体不因单条失败而回退。

        返回结构同时给出每条的 success/skipped/failed 结论与扣减前后结存。
        同一批材料（相同 batch_no，或相同 动作+条目+数量 指纹）重复提交时，
        直接回放首次结果，不再扣减库存、不产生第二条处理记录。
        """
        action = (action or "").strip()
        if action not in BATCH_ACTIONS:
            raise ValueError(f"动作「{action}」不属于养护材料可批量执行范围")
        if not items:
            raise ValueError("请至少勾选一条养护材料再提交")
        ids = [item_id for item_id, _ in items]
        if len(set(ids)) != len(ids):
            raise ValueError("同一批材料里存在重复勾选，请去重后再提交")

        fingerprint = self._fingerprint(action, items)
        batch_no = (batch_no or "").strip() or fingerprint

        if batch_no in self._batches_by_key:
            if self._batches_by_key[batch_no]["fingerprint"] != fingerprint:
                raise ValueError(f"批次号 {batch_no} 已用于另一批材料，请重新发起一批")
            return self._as_replay(self._batches_by_key[batch_no]["result"])
        if fingerprint in self._fingerprint_keys:
            return self._as_replay(self._batches_by_key[self._fingerprint_keys[fingerprint]]["result"])

        results: list[dict[str, Any]] = []
        for item_id, quantity in items:
            entry = store.find(MODULE, item_id)
            if entry is None:
                results.append({
                    "id": item_id, "code": None, "name": None,
                    "outcome": OUTCOME_FAILED,
                    "reason": f"养护材料 {item_id} 不存在或已归档，未做处理",
                    "quantity": quantity,
                    "before_balance": None, "after_balance": None,
                })
                continue
            before = int(entry.get("结存数量", 0) or 0)
            code = entry.get("材料编号")
            name = entry.get("材料名称")
            try:
                item_result = self._process(entry, action, quantity)
            except Exception as exc:  # 单条异常只记失败，绝不拖累同批其他条目
                item_result = {
                    "outcome": OUTCOME_FAILED,
                    "reason": f"处理异常：{exc}",
                    "before_balance": before,
                    "after_balance": int(entry.get("结存数量", 0) or 0),
                }
            self._refresh(entry)
            results.append({
                "id": item_id,
                "code": code,
                "name": name,
                "outcome": item_result["outcome"],
                "reason": item_result["reason"],
                "quantity": quantity if action == "领用出库" else None,
                "before_balance": item_result.get("before_balance"),
                "after_balance": item_result.get("after_balance"),
            })

        success_count = sum(1 for r in results if r["outcome"] == OUTCOME_SUCCESS)
        skipped_count = sum(1 for r in results if r["outcome"] == OUTCOME_SKIPPED)
        failed_count = sum(1 for r in results if r["outcome"] == OUTCOME_FAILED)
        entries = [self._refresh(dict(row)) for row in store.rows(MODULE) if int(row.get("id", 0)) in set(ids)]
        entries.sort(key=lambda row: ids.index(int(row["id"])))

        if failed_count == 0 and skipped_count == 0:
            message = f"{action}完成：{success_count} 条全部生效"
        elif failed_count == 0:
            message = f"{action}结束：成功 {success_count} 条，自动跳过 {skipped_count} 条"
        else:
            message = (
                f"{action}结束：成功 {success_count} 条，自动跳过 {skipped_count} 条，"
                f"失败 {failed_count} 条；失败条目未通过，其余条目已照常生效且未回退"
            )

        result = {
            "ok": failed_count == 0,
            "message": message,
            "action": action,
            "batch_no": batch_no,
            "replayed": False,
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "results": results,
            "entries": entries,
        }
        self._batches_by_key[batch_no] = {"fingerprint": fingerprint, "result": result}
        self._fingerprint_keys[fingerprint] = batch_no
        return result

    # ------------------------------------------------------------------ 内部

    def _process(self, entry: dict[str, Any], action: str, quantity: int | None) -> dict[str, Any]:
        """对单条材料执行一个动作，返回结论说明；成功/跳过类结论会就地修改材料。"""
        balance = int(entry.get("结存数量", 0) or 0)
        status = entry.get("status")
        hint = ACTION_TARGET_HINT.get(action, action)

        if action == "领用出库":
            if quantity is None:
                return self._fail(balance, "未填写领用数量，未扣减库存")
            if quantity <= 0:
                return self._fail(balance, f"领用数量 {quantity} 非法（须为正整数），未扣减库存")
            if status == STATUS_EMPTY or balance <= 0:
                return self._skip(balance, "材料已耗尽，自动跳过，未扣减库存")
            if status == STATUS_FROZEN:
                return self._fail(balance, "材料已冻结，不可领用，未扣减库存")
            if quantity > balance:
                return self._fail(balance, f"可用数量仅 {balance}，不足本次领用 {quantity}，未扣减库存")
            entry["结存数量"] = balance - quantity
            after = int(entry["结存数量"])
            refreshed = self._refresh(entry)
            floor = int(entry["储备下限"])
            crossed_floor = balance >= floor and after < floor
            return {
                "outcome": OUTCOME_SUCCESS,
                "reason": f"领用成功：扣减 {quantity}，结存 {balance} → {after}"
                + ("，已跌破储备下限并自动转为「临近不足」" if crossed_floor and refreshed["status"] == STATUS_LOW else ""),
                "before_balance": balance,
                "after_balance": after,
            }

        if action == "冻结材料":
            if status == STATUS_EMPTY or balance <= 0:
                return self._skip(balance, "材料已耗尽，冻结时自动跳过")
            if status == STATUS_FROZEN:
                return self._skip(balance, "材料已冻结，无需重复冻结")
            entry["status"] = STATUS_FROZEN
            self._refresh(entry)
            return {"outcome": OUTCOME_SUCCESS, "reason": "材料已冻结，可用数量置为 0",
                    "before_balance": balance, "after_balance": balance}

        if action == "解冻材料":
            if status != STATUS_FROZEN:
                return self._fail(balance, "材料未处于冻结状态，无需解冻")
            entry["status"] = self._base_status(entry)
            self._refresh(entry)
            return {"outcome": OUTCOME_SUCCESS,
                    "reason": f"材料已解冻，恢复为「{entry['status']}」，可用数量恢复为 {entry['可用数量']}",
                    "before_balance": balance, "after_balance": balance}

        if action == "登记耗尽":
            if status == STATUS_EMPTY or balance <= 0:
                return self._skip(balance, "材料已登记耗尽，无需重复处理")
            if status == STATUS_FROZEN:
                return self._fail(balance, "材料已冻结，请先解冻再登记耗尽，未做改动")
            entry["结存数量"] = 0
            self._refresh(entry)
            return {"outcome": OUTCOME_SUCCESS,
                    "reason": f"已登记耗尽：结存 {balance} → 0",
                    "before_balance": balance, "after_balance": 0}

        return self._fail(balance, f"动作「{action}」不被支持")

    def _fail(self, balance: int, reason: str) -> dict[str, Any]:
        return {"outcome": OUTCOME_FAILED, "reason": reason,
                "before_balance": balance, "after_balance": balance}

    def _skip(self, balance: int, reason: str) -> dict[str, Any]:
        return {"outcome": OUTCOME_SKIPPED, "reason": reason,
                "before_balance": balance, "after_balance": balance}

    def _base_status(self, entry: dict[str, Any]) -> str:
        """只按结存数量与储备下限推导状态，不考虑冻结标记。"""
        balance = int(entry.get("结存数量", 0) or 0)
        floor = int(entry.get("储备下限", DEFAULT_RESERVE_FLOOR) or 0)
        if balance <= 0:
            return STATUS_EMPTY
        if balance < floor:
            return STATUS_LOW
        return STATUS_NORMAL

    def _refresh(self, entry: dict[str, Any]) -> dict[str, Any]:
        """按结存数量校准状态、可用数量、看板标记；冻结态在有库存时保持冻结。"""
        balance = _to_int(entry.get("结存数量"))
        if balance is None or balance < 0:
            balance = 0
        floor = _to_int(entry.get("储备下限"))
        if floor is None or floor < 0:
            floor = DEFAULT_RESERVE_FLOOR
        entry["结存数量"] = balance
        entry["储备下限"] = floor

        base = self._base_status(entry)
        if entry.get("status") == STATUS_FROZEN and base != STATUS_EMPTY:
            entry["status"] = STATUS_FROZEN
        else:
            entry["status"] = base
        # 冻结、耗尽材料不对外释放可用数量；其余材料可用数量即结存数量。
        entry["可用数量"] = 0 if entry["status"] in (STATUS_FROZEN, STATUS_EMPTY) else balance
        entry["pending"] = entry["status"] in (STATUS_NORMAL, STATUS_LOW)
        entry["abnormal"] = entry["status"] in (STATUS_LOW, STATUS_EMPTY)
        # 列表里的「材料状态」展示列与内部 status 保持同一份文案。
        entry["材料状态"] = entry["status"]
        return entry

    def _fingerprint(self, action: str, items: list[tuple[int, int | None]]) -> str:
        raw = action + "|" + ",".join(f"{item_id}:{quantity if quantity is not None else ''}"
                                      for item_id, quantity in sorted(items))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _as_replay(self, first_result: dict[str, Any]) -> dict[str, Any]:
        replay = dict(first_result)
        replay["replayed"] = True
        replay["message"] = (
            f"该批次已提交过（{first_result['action']}，批次号 {first_result['batch_no']}），"
            "未重复扣减库存，以下为首次处理结果"
        )
        return replay
