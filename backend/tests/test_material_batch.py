"""养护材料批量处理规则验证（纯标准库，不依赖 pytest）。

覆盖：
1. 批量领用逐条给结论：成功 / 自动跳过（已耗尽）/ 失败（冻结、超量、非法数量、不存在）；
2. 一条不通过其余照常生效、不回退；
3. 跌破储备下限自动落入「临近不足」，临近不足材料照常参与；
4. 同一批材料重复提交只回放首次结果，只扣一次库存、只留一次处理结果；
5. 批量冻结自动跳过已耗尽材料；
6. 库存看板结存/可用数量与材料记录一致。

运行：.venv/bin/python -m tests.test_material_batch
"""
from __future__ import annotations

import sys
import unittest

from app.seed import SEED_ROWS
from app.services import material as material_module
from app.services.material import (
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_SUCCESS,
    MaterialService,
)
from app.store import store

SUCCESS = OUTCOME_SUCCESS
SKIPPED = OUTCOME_SKIPPED
FAILED = OUTCOME_FAILED


def reset_store() -> None:
    """把内存仓库重置回种子数据，并重建一个干净的材料服务（清空幂等记录）。"""
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    # 路由层持有的是单例；直接覆盖单例内部状态即可。
    service = _service
    service._batches_by_key.clear()
    service._fingerprint_keys.clear()
    for row in store.rows("material"):
        service._refresh(row)


_service = MaterialService()


def by_id(results: list[dict], entry_id: int) -> dict:
    return next(result for result in results if result["id"] == entry_id)


class MaterialBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()

    def test_board_seed_inventory(self) -> None:
        board = _service.inventory()
        cards = {card["label"]: card["value"] for card in board["cards"]}
        self.assertEqual(cards["材料种类"], 6)
        # 临近不足（2、6）结存仍可领用：1、2、5、6 共 4 种可领用
        self.assertEqual(cards["可领用材料"], 4)
        self.assertEqual(cards["临近不足"], 2)
        self.assertEqual(cards["已冻结"], 1)
        self.assertEqual(cards["已耗尽"], 1)
        self.assertEqual(cards["结存总量合计"], 120 + 8 + 60 + 0 + 25 + 5)
        self.assertEqual(cards["可用总量合计"], 120 + 8 + 0 + 0 + 25 + 5)
        items = {int(row["id"]): row for row in board["items"]}
        self.assertEqual(items[3]["可用数量"], 0)  # 已冻结可用为 0
        self.assertEqual(items[4]["可用数量"], 0)  # 已耗尽可用为 0
        self.assertEqual(items[3]["结存数量"], 60)  # 冻结不改动结存

    def test_batch_consume_mixed_outcomes_no_rollback(self) -> None:
        # id1 领 50 成功(120→70)；id2 领 8 成功且仍低于下限(8→0 → 已耗尽)；
        # id3 冻结失败；id4 耗尽跳过；id5 领 100 超量失败；id9 不存在失败；id6 领 6 超量失败。
        result = _service.batch_run("领用出库", [
            (1, 50), (2, 8), (3, 1), (4, 1), (5, 100), (9, 1), (6, 6),
        ], batch_no="B-001")

        self.assertFalse(result["ok"])
        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["failed_count"], 4)

        self.assertEqual(by_id(result["results"], 1)["outcome"], SUCCESS)
        self.assertEqual(store.find("material", 1)["结存数量"], 70)

        # id2 结存 8 领 8 后掉到 0：自动落入已耗尽
        self.assertEqual(by_id(result["results"], 2)["outcome"], SUCCESS)
        self.assertEqual(store.find("material", 2)["结存数量"], 0)
        self.assertEqual(store.find("material", 2)["status"], "已耗尽")
        self.assertEqual(store.find("material", 2)["可用数量"], 0)

        self.assertEqual(by_id(result["results"], 3)["outcome"], FAILED)
        self.assertIn("冻结", by_id(result["results"], 3)["reason"])
        self.assertEqual(store.find("material", 3)["结存数量"], 60)  # 未扣减

        self.assertEqual(by_id(result["results"], 4)["outcome"], SKIPPED)
        self.assertIn("已耗尽", by_id(result["results"], 4)["reason"])

        self.assertEqual(by_id(result["results"], 5)["outcome"], FAILED)
        self.assertIn("不足", by_id(result["results"], 5)["reason"])
        self.assertEqual(store.find("material", 5)["结存数量"], 25)  # 失败不扣减

        self.assertEqual(by_id(result["results"], 9)["outcome"], FAILED)
        self.assertEqual(by_id(result["results"], 6)["outcome"], FAILED)

        # 部分失败不回退：成功的 1、2 保持扣减后状态；每条都带原因
        self.assertEqual(store.find("material", 1)["结存数量"], 70)
        for item in result["results"]:
            self.assertTrue(item["reason"])

    def test_cross_reserve_floor_auto_low_still_consumable(self) -> None:
        # id5 结存 25、下限 10：领 20 后结存 5，自动转入临近不足，卡片可用随之更新
        result = _service.batch_run("领用出库", [(5, 20)], batch_no="B-002")
        self.assertTrue(result["ok"])
        entry = store.find("material", 5)
        self.assertEqual(entry["结存数量"], 5)
        self.assertEqual(entry["status"], "临近不足")
        self.assertEqual(entry["可用数量"], 5)
        self.assertIn("临近不足", by_id(result["results"], 5)["reason"])

        # 临近不足材料照常参与下一批领用
        again = _service.batch_run("领用出库", [(5, 3)], batch_no="B-003")
        self.assertEqual(by_id(again["results"], 5)["outcome"], SUCCESS)
        self.assertEqual(store.find("material", 5)["结存数量"], 2)
        self.assertEqual(store.find("material", 5)["可用数量"], 2)

    def test_idempotent_same_batch_no_only_once(self) -> None:
        first = _service.batch_run("领用出库", [(1, 40)], batch_no="DUP-1")
        self.assertEqual(first["replayed"], False)
        self.assertEqual(store.find("material", 1)["结存数量"], 80)

        # 完全相同的请求再次提交：回放、不二次扣减、处理结果只留一次
        second = _service.batch_run("领用出库", [(1, 40)], batch_no="DUP-1")
        self.assertTrue(second["replayed"])
        self.assertEqual(second["success_count"], 1)
        self.assertEqual(store.find("material", 1)["结存数量"], 80)
        self.assertEqual(len(_service._batches_by_key), 1)
        self.assertEqual(len(_service._fingerprint_keys), 1)

    def test_idempotent_same_fingerprint_without_batch_no(self) -> None:
        # 页面重提交时即便没带批次号，相同 动作+条目+数量 也按重复处理
        _service.batch_run("领用出库", [(1, 10), (6, 2)])
        self.assertEqual(store.find("material", 1)["结存数量"], 110)
        self.assertEqual(store.find("material", 6)["结存数量"], 3)
        replay = _service.batch_run("领用出库", [(1, 10), (6, 2)])
        self.assertTrue(replay["replayed"])
        self.assertEqual(store.find("material", 1)["结存数量"], 110)
        self.assertEqual(store.find("material", 6)["结存数量"], 3)

    def test_batch_no_collision_different_content_rejected(self) -> None:
        _service.batch_run("领用出库", [(1, 10)], batch_no="X-1")
        with self.assertRaises(ValueError):
            _service.batch_run("领用出库", [(2, 10)], batch_no="X-1")

    def test_batch_freeze_skips_exhausted(self) -> None:
        # 勾选 id1（正常）、id4（已耗尽）、id3（已冻结）一起冻结
        result = _service.batch_run("冻结材料", [(1, None), (4, None), (3, None)], batch_no="B-FRZ")
        outcomes = {item["id"]: item["outcome"] for item in result["results"]}
        self.assertEqual(outcomes[1], SUCCESS)
        self.assertEqual(outcomes[4], SKIPPED)  # 冻结时自动跳过已耗尽
        self.assertEqual(outcomes[3], SKIPPED)  # 重复冻结按跳过处理
        self.assertEqual(store.find("material", 1)["status"], "已冻结")
        self.assertEqual(store.find("material", 1)["可用数量"], 0)
        self.assertEqual(store.find("material", 4)["status"], "已耗尽")

    def test_unfreeze_restores_availability_by_balance(self) -> None:
        _service.batch_run("解冻材料", [(3, None)], batch_no="B-UFZ")
        entry = store.find("material", 3)
        self.assertEqual(entry["status"], "正常可用")
        self.assertEqual(entry["可用数量"], 60)

    def test_register_exhausted_batch(self) -> None:
        result = _service.batch_run("登记耗尽", [(5, None), (4, None)], batch_no="B-EMP")
        outcomes = {item["id"]: item["outcome"] for item in result["results"]}
        self.assertEqual(outcomes[5], SUCCESS)
        self.assertEqual(outcomes[4], SKIPPED)  # 已耗尽自动跳过
        self.assertEqual(store.find("material", 5)["结存数量"], 0)
        self.assertEqual(store.find("material", 5)["status"], "已耗尽")

    def test_request_level_validation(self) -> None:
        with self.assertRaises(ValueError):
            _service.batch_run("领用出库", [], batch_no="B-EMPTY")
        with self.assertRaises(ValueError):
            _service.batch_run("未知动作", [(1, 1)], batch_no="B-BAD")
        with self.assertRaises(ValueError):
            _service.batch_run("领用出库", [(1, 1), (1, 2)], batch_no="B-DUPID")

    def test_invalid_and_nonpositive_quantity_failed(self) -> None:
        result = _service.batch_run("领用出库", [(1, 0), (5, -3)], batch_no="B-QTY")
        self.assertTrue(all(item["outcome"] == FAILED for item in result["results"]))
        self.assertEqual(store.find("material", 1)["结存数量"], 120)
        self.assertEqual(store.find("material", 5)["结存数量"], 25)

    def test_board_reflects_latest_balances(self) -> None:
        _service.batch_run("领用出库", [(1, 100), (5, 20)], batch_no="B-BOARD")
        board = _service.inventory()
        items = {int(row["id"]): row for row in board["items"]}
        # id1 结存 20 仍 >= 下限 20，保持正常；id5 掉到 5，临近不足
        self.assertEqual(items[1]["结存数量"], 20)
        self.assertEqual(items[1]["可用数量"], 20)
        self.assertEqual(items[1]["材料状态"], "正常可用")
        self.assertEqual(items[5]["结存数量"], 5)
        self.assertEqual(items[5]["可用数量"], 5)
        self.assertEqual(items[5]["材料状态"], "临近不足")
        # 看板可用合计与领用界面逐条可用之和完全一致
        cards = {card["label"]: card["value"] for card in board["cards"]}
        self.assertEqual(cards["可用总量合计"], sum(int(row["可用数量"]) for row in board["items"]))
        self.assertEqual(cards["结存总量合计"], sum(int(row["结存数量"]) for row in board["items"]))

    def test_find_batch_returns_first_result(self) -> None:
        first = _service.batch_run("领用出库", [(6, 1)], batch_no="B-FIND")
        fetched = _service.find_batch("B-FIND")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["batch_no"], "B-FIND")
        self.assertEqual(fetched["results"], first["results"])
        self.assertIsNone(_service.find_batch("NOT-EXIST"))


if __name__ == "__main__":
    # 用 verbosity=2 逐条打印结果，便于人工核对成功/失败原因。
    unittest.main(verbosity=2, exit=True)
