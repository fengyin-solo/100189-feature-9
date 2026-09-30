"""路由层冒烟验证（不依赖 fastapi/pydantic 安装包）。

当前容器的 Python 没有安装 fastapi/pydantic，这里用最小桩模块替代，
直接调用路由函数，验证：请求级 400 校验、批量响应结构、库存看板、批次回查。
真正的 FastAPI 运行环境通过 run.sh 安装 requirements.txt 后即可启动。
"""
from __future__ import annotations

import sys
import types
import unittest


# ---------------------------------------------------------------- 桩模块

class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def Query(default=None, description=None):  # noqa: ANN001
    return default


class APIRouter:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get(self, *args, **kwargs):  # noqa: ANN002
        def wrap(func):
            return func
        return wrap

    def post(self, *args, **kwargs):  # noqa: ANN002
        def wrap(func):
            return func
        return wrap


fastapi_stub = types.ModuleType("fastapi")
fastapi_stub.APIRouter = APIRouter
fastapi_stub.HTTPException = HTTPException
fastapi_stub.Query = Query
sys.modules["fastapi"] = fastapi_stub


class _Attr(dict):
    """让嵌套字典支持属性访问，模拟 pydantic 嵌套模型。"""

    def __getattr__(self, name):  # noqa: ANN001
        value = self[name]
        return _wrap(value)


def _wrap(value):  # noqa: ANN001
    if isinstance(value, dict):
        return _Attr(value)
    if isinstance(value, list):
        return [_wrap(item) for item in value]
    return value


class BaseModel:
    """pydantic.BaseModel 的最小替身：支持 kwargs 构造与 model_validate。"""

    def __init__(self, **values) -> None:  # noqa: ANN001
        annotations = {}
        for cls in type(self).__mro__:
            annotations.update(getattr(cls, "__annotations__", {}))
        for key in annotations:
            if key in values:
                setattr(self, key, _wrap(values[key]))
            else:
                setattr(self, key, None)
        for key, value in values.items():
            if key not in annotations:
                setattr(self, key, _wrap(value))

    @classmethod
    def model_validate(cls, data):  # noqa: ANN001
        return cls(**data)


def Field(default=None, default_factory=None, **kwargs):  # noqa: ANN001
    return default_factory() if default_factory is not None else default


class _GenericMeta(type):
    def __getitem__(cls, item):  # noqa: ANN001
        return cls


Generic = type("Generic", (), {"__class_getitem__": classmethod(lambda cls, item: cls)})

pydantic_stub = types.ModuleType("pydantic")
pydantic_stub.BaseModel = BaseModel
pydantic_stub.Field = Field
sys.modules["pydantic"] = pydantic_stub

# typing 扩展：测试环境直接使用标准库 Generic/TypeVar 即可，无需替换。

from app.seed import SEED_ROWS  # noqa: E402
from app.routers import material as router_module  # noqa: E402
from app.schemas import BatchActionPayload, BatchItemPayload  # noqa: E402
from app.services import material as material_service  # noqa: E402
from app.store import store  # noqa: E402


def reset_store() -> None:
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    service = router_module.service
    service._batches_by_key.clear()
    service._fingerprint_keys.clear()
    for row in store.rows("material"):
        service._refresh(row)


class RouterSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()

    def test_batch_endpoint_partial_failure_returns_200_shape(self) -> None:
        payload = BatchActionPayload(
            action="领用出库",
            items=[
                BatchItemPayload(id=1, quantity=50),   # 成功
                BatchItemPayload(id=3, quantity=1),    # 冻结失败
                BatchItemPayload(id=4, quantity=1),    # 耗尽跳过
            ],
            batch_no="SMOKE-1",
        )
        result = router_module.run_batch(payload)
        self.assertFalse(result.ok)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(len(result.results), 3)
        self.assertEqual(result.entries[0]["结存数量"], 70)

    def test_batch_endpoint_bad_requests_raise_400(self) -> None:
        cases = [
            BatchActionPayload(action="领用出库", items=[]),
            BatchActionPayload(action="不存在", items=[BatchItemPayload(id=1)]),
        ]
        for payload in cases:
            with self.assertRaises(HTTPException) as ctx:
                router_module.run_batch(payload)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_batch_endpoint_replay_not_deducted_again(self) -> None:
        payload = BatchActionPayload(action="领用出库", items=[BatchItemPayload(id=1, quantity=40)],
                                     batch_no="SMOKE-DUP")
        first = router_module.run_batch(payload)
        self.assertFalse(first.replayed)
        second = router_module.run_batch(payload)
        self.assertTrue(second.replayed)
        self.assertEqual(store.find("material", 1)["结存数量"], 80)

    def test_inventory_endpoint(self) -> None:
        board = router_module.inventory_board()
        labels = [card.label for card in board.cards]
        self.assertIn("结存总量合计", labels)
        self.assertIn("可用总量合计", labels)
        self.assertEqual(len(board.items), 6)

    def test_get_batch_endpoint_404_and_hit(self) -> None:
        payload = BatchActionPayload(action="冻结材料", items=[BatchItemPayload(id=1)], batch_no="SMOKE-GET")
        router_module.run_batch(payload)
        fetched = router_module.get_batch("SMOKE-GET")
        self.assertEqual(fetched.batch_no, "SMOKE-GET")
        with self.assertRaises(HTTPException) as ctx:
            router_module.get_batch("MISSING")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_single_action_endpoint_freeze_exhausted_is_skipped_not_failed(self) -> None:
        payload = types.SimpleNamespace(values={"action": "冻结材料"})
        result = router_module.run_action(4, payload)  # id4 已耗尽
        self.assertTrue(result.ok)
        self.assertIn("自动跳过", result.message)

    def test_list_endpoint_paginates_and_canonicalizes(self) -> None:
        page = router_module.list_entries(status="临近不足", page=1, size=10)
        self.assertEqual(page.total, 2)
        self.assertEqual({row["材料编号"] for row in page.items}, {"MATE-0002", "MATE-0006"})
        for row in page.items:
            self.assertEqual(row["材料状态"], row["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
