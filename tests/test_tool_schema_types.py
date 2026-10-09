"""工具 schema 类型支持与传参校验：Optional/Literal/嵌套结构，纠正是为了少一次失败往返"""
import dataclasses
import unittest
from enum import Enum
from typing import Dict, List, Literal, Optional, TypedDict
from unittest.mock import MagicMock

from app.tools.registry import (
    _TOOL_REGISTRY,
    _python_type_to_json_schema,
    get_tool_registry,
    tool,
)


class Color(Enum):
    RED = "red"
    GREEN = "green"


@dataclasses.dataclass
class Point:
    x: int
    y: int
    label: str = ""


class _PairRequired(TypedDict):
    remove: str
    add: str


class Pair(_PairRequired, total=False):
    weight: float


class TestTypeMapping(unittest.TestCase):
    """注解 → JSON Schema"""

    def test_scalars(self):
        self.assertEqual(_python_type_to_json_schema(int), {"type": "integer"})
        self.assertEqual(_python_type_to_json_schema(float), {"type": "number"})
        self.assertEqual(_python_type_to_json_schema(bool), {"type": "boolean"})
        self.assertEqual(_python_type_to_json_schema(str), {"type": "string"})

    def test_bare_containers_unchanged(self):
        self.assertEqual(_python_type_to_json_schema(list),
                         {"type": "array", "items": {"type": "string"}})
        self.assertEqual(_python_type_to_json_schema(dict), {"type": "object"})

    def test_optional_marks_nullable(self):
        schema = _python_type_to_json_schema(Optional[int])
        self.assertEqual(schema, {"type": "integer", "nullable": True})

    def test_pep604_union_marks_nullable(self):
        self.assertEqual(_python_type_to_json_schema(str | None),
                         {"type": "string", "nullable": True})

    def test_union_without_none_is_not_nullable(self):
        self.assertFalse(_python_type_to_json_schema(int | str).get("nullable", False))

    def test_literal_becomes_enum(self):
        schema = _python_type_to_json_schema(Literal["buy", "sell"])

        self.assertEqual(schema, {"enum": ["buy", "sell"], "type": "string"})

    def test_enum_class_becomes_enum(self):
        self.assertEqual(_python_type_to_json_schema(Color),
                         {"enum": ["red", "green"], "type": "string"})

    def test_typed_dict_nested_properties(self):
        schema = _python_type_to_json_schema(Pair)
        self.assertEqual(schema["type"], "object")
        self.assertEqual(sorted(schema["properties"]), ["add", "remove", "weight"])
        self.assertEqual(schema["properties"]["weight"], {"type": "number"})
        self.assertEqual(sorted(schema["required"]), ["add", "remove"])

    def test_dataclass_nested_properties(self):
        schema = _python_type_to_json_schema(Point)
        self.assertEqual(schema["properties"]["x"], {"type": "integer"})
        self.assertEqual(sorted(schema["required"]), ["x", "y"])

    def test_list_of_typed_dict(self):
        schema = _python_type_to_json_schema(List[Pair])
        self.assertEqual(schema["type"], "array")
        self.assertEqual(schema["items"]["properties"]["add"], {"type": "string"})

    def test_dict_with_value_type(self):
        schema = _python_type_to_json_schema(Dict[str, float])
        self.assertEqual(schema, {"type": "object",
                                  "additionalProperties": {"type": "number"}})

    def test_unknown_annotation_falls_back_to_string(self):
        self.assertEqual(_python_type_to_json_schema(object), {"type": "string"})


class TestSuggestionToolSchema(unittest.TestCase):
    """真实工具：swaps 必须带结构，否则 LLM 只能猜格式"""

    def setUp(self):
        get_tool_registry()

    def test_swaps_schema_has_structure(self):
        swaps = _TOOL_REGISTRY["suggest_allocation_change"].parameters["properties"]["swaps"]

        self.assertEqual(swaps["type"], "array")
        self.assertEqual(sorted(swaps["items"]["properties"]),
                         ["add", "reason", "remove", "weight"])
        self.assertEqual(sorted(swaps["items"]["required"]), ["add", "remove"])
        self.assertNotIn("default", swaps, "None 默认值不应写进 schema")

    def test_required_params_listed(self):
        params = _TOOL_REGISTRY["suggest_allocation_change"].parameters

        self.assertEqual(params["required"], ["strategy_id", "new_allocation"])
        self.assertEqual(params["properties"]["new_allocation"]["type"], "object")


TEMP_TOOLS = []


def register_temp_tool(name: str, func):
    """注册临时工具，测试后移除，避免污染全局注册表"""
    tool(name=name, description="测试工具", risk="read")(func)
    TEMP_TOOLS.append(name)


class TestArgumentValidation(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.registry = get_tool_registry()
        self.calls = []

        def sample(db, strategy_id: int, limit: int = 5,
                   dry_run: bool = False, codes: List[str] = None,
                   weights: Dict[str, float] = None, note: str = ""):
            self.calls.append({"db": db, "strategy_id": strategy_id, "limit": limit,
                               "dry_run": dry_run, "codes": codes,
                               "weights": weights, "note": note})
            return {"ok": True}

        register_temp_tool("test_sample_tool", sample)

    def tearDown(self):
        for name in TEMP_TOOLS:
            _TOOL_REGISTRY.pop(name, None)
        TEMP_TOOLS.clear()

    def _run(self, arguments):
        return self.registry.execute("test_sample_tool", arguments, self.db)

    def test_valid_call_injects_db_and_passes_args(self):
        result = self._run({"strategy_id": 1, "codes": ["510300"]})

        self.assertEqual(result, {"ok": True})
        self.assertIs(self.calls[0]["db"], self.db)
        self.assertEqual(self.calls[0]["strategy_id"], 1)
        self.assertEqual(self.calls[0]["codes"], ["510300"])

    def test_unknown_argument_rejected_with_hint(self):
        result = self._run({"strategy_id": 1, "allocaton": {}})

        self.assertIn("不属于工具", result["error"])
        self.assertIn("allocaton", result["error"])
        self.assertIn("strategy_id", result["error"])
        self.assertEqual(self.calls, [])

    def test_missing_required_argument(self):
        result = self._run({"limit": 3})

        self.assertIn("缺少必填参数", result["error"])
        self.assertIn("strategy_id", result["error"])

    def test_string_numbers_are_coerced(self):
        self._run({"strategy_id": "7", "limit": 12.0})

        self.assertEqual(self.calls[0]["strategy_id"], 7)
        self.assertEqual(self.calls[0]["limit"], 12)

    def test_fractional_integer_rejected(self):
        result = self._run({"strategy_id": 1, "limit": 2.5})

        self.assertIn("limit", result["error"])
        self.assertIn("类型不符", result["error"])

    def test_bool_from_string(self):
        self._run({"strategy_id": 1, "dry_run": "true"})

        self.assertIs(self.calls[0]["dry_run"], True)

    def test_json_string_for_array_and_object(self):
        self._run({"strategy_id": 1, "codes": '["510300", "510500"]',
                   "weights": '{"510300": 0.6}'})

        self.assertEqual(self.calls[0]["codes"], ["510300", "510500"])
        self.assertEqual(self.calls[0]["weights"], {"510300": 0.6})

    def test_unparseable_json_string_rejected(self):
        result = self._run({"strategy_id": 1, "codes": "510300, 510500"})

        self.assertIn("codes", result["error"])
        self.assertIn("期望 array", result["error"])

    def test_int_for_string_param_is_accepted_as_code(self):
        self._run({"strategy_id": 1, "note": 510300})

        self.assertEqual(self.calls[0]["note"], "510300")

    def test_object_param_receives_list(self):
        result = self._run({"strategy_id": 1, "weights": [1, 2]})

        self.assertIn("weights", result["error"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
