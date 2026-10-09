"""
Tool Registry - LLM 工具注册中心

提供 @tool 装饰器注册工具，自动生成 OpenAI Function Calling schema，
并支持按名称执行工具。
"""

import dataclasses
import enum
import inspect
import json
import logging
import re
import types
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Union, get_args, get_origin, get_type_hints
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 全局工具注册表
_TOOL_REGISTRY: Dict[str, "ToolDef"] = {}

# 基础标量 → JSON Schema 类型
_SCALAR_TYPES = {int: "integer", float: "number", bool: "boolean", str: "string"}

# 被视为数组的容器类型（LLM 传 tuple/set 时也按 array 校验）
_SEQUENCE_ORIGINS = (list, set, frozenset, tuple)

# docstring 中 Args/参数 段的结束标题
_DOC_STOP_HEADINGS = {
    "Returns:", "Return:", "返回:", "Raises:", "抛出:",
    "Example:", "Examples:", "示例:", "Note:", "Notes:", "注意:", "说明:",
}


def _parse_docstring_params(func: Callable) -> Dict[str, str]:
    """从 docstring 的 Args:/参数: 段解析 参数名 → 描述"""
    doc = inspect.getdoc(func) or ""
    result: Dict[str, str] = {}
    in_args = False
    for line in doc.splitlines():
        s = line.strip()
        if s in ("Args:", "参数:", "Parameters:"):
            in_args = True
            continue
        if not in_args or not s:
            continue
        if s in _DOC_STOP_HEADINGS:
            break
        m = re.match(r"^[-*]?\s*(\w+)\s*[:：,]\s*(.+)$", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _unwrap_annotated(annotation):
    """解包 Annotated[type, "描述"]，返回 (基础类型, 描述或None)"""
    meta = getattr(annotation, "__metadata__", None)
    if meta and getattr(annotation, "__origin__", None) is not None:
        desc = meta[0] if meta and isinstance(meta[0], str) else None
        return annotation.__origin__, desc
    return annotation, None

# 未显式声明风险时，按名称归类的写操作工具（变更系统状态，需审批）
_WRITE_TOOLS: Set[str] = {
    "create_strategy",
    "delete_strategy",
    "pause_strategy",
    "resume_strategy",
    "add_etf_to_pool",
    "execute_rebalance",
    "sync_market_data",
    "trigger_review",
    "trigger_daily_pipeline",
    "trigger_sentiment_collect",
    "catch_up_strategy",
    "fetch_etf_history",
}


class ToolDef:
    """工具定义"""

    def __init__(self, name: str, description: str, func: Callable, parameters: Dict,
                 risk_level: str = "read", requires_approval: bool = False):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters  # JSON Schema
        self.risk_level = risk_level  # read / write
        self.requires_approval = requires_approval

    def to_openai_schema(self) -> Dict:
        """转换为 OpenAI tools 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _object_schema_from_members(fields: List[tuple], required: Set[str]) -> Dict:
    """由 (名称, 注解) 列表与必填集合构造 object schema（TypedDict / dataclass 共用）"""
    properties = {}
    for field_name, annotation in fields:
        properties[field_name] = _python_type_to_json_schema(annotation)
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    ordered_required = [n for n, _ in fields if n in required]
    if ordered_required:
        schema["required"] = ordered_required
    return schema


def _python_type_to_json_schema(annotation) -> Dict:
    """将 Python 类型注解转为 JSON Schema 类型

    支持标量、Optional/X|None、Literal、Enum、list[X]、dict[K,V]、TypedDict 与 dataclass；
    无法识别时退回 {"type": "string"}（与历史行为一致）。
    """
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if annotation is None or annotation is type(None):
        return {"type": "null"}
    if annotation in _SCALAR_TYPES:
        return {"type": _SCALAR_TYPES[annotation]}

    if inspect.isclass(annotation):
        # 裸容器类型（无泛型参数）保持历史口径：array 元素未知按 string
        if annotation in _SEQUENCE_ORIGINS:
            return {"type": "array", "items": {"type": "string"}}
        if annotation is dict:
            return {"type": "object"}
        if issubclass(annotation, enum.Enum):
            values = [member.value for member in annotation]
            types_ = {_SCALAR_TYPES.get(type(v)) for v in values}
            schema: Dict[str, Any] = {"enum": values}
            schema["type"] = types_.pop() if len(types_) == 1 else "string"
            return schema
        if typing.is_typeddict(annotation):
            return _object_schema_from_members(
                list(get_type_hints(annotation, include_extras=True).items()),
                set(annotation.__required_keys__),
            )
        if dataclasses.is_dataclass(annotation):
            fields = [(f.name, f.type) for f in dataclasses.fields(annotation)
                      if f.init and not f.name.startswith("_")]
            required = {f.name for f in dataclasses.fields(annotation)
                        if f.init and f.default is dataclasses.MISSING
                        and f.default_factory is dataclasses.MISSING}
            return _object_schema_from_members(fields, required)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is typing.Literal:
        types_ = {_SCALAR_TYPES.get(type(a)) for a in args}
        schema = {"enum": list(args)}
        schema["type"] = types_.pop() if len(types_) == 1 else "string"
        return schema

    if origin is Union or origin is getattr(types, "UnionType", None):
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) == 1:
            schema = _python_type_to_json_schema(non_null[0])
        elif non_null:
            # 多类型联合：只保留能公共表达的信息（enum 合并）
            schemas = [_python_type_to_json_schema(a) for a in non_null]
            enums = [s["enum"] for s in schemas if "enum" in s]
            schema = {"enum": [v for group in enums for v in group]} if enums \
                else {"type": "string"}
        else:
            schema = {"type": "null"}
        if len(non_null) != len(args):
            schema["nullable"] = True
        return schema

    if origin in _SEQUENCE_ORIGINS:
        items = _python_type_to_json_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": items}

    if origin is dict:
        schema = {"type": "object"}
        if args:
            schema["additionalProperties"] = _python_type_to_json_schema(args[1])
        return schema

    return {"type": "string"}


def tool(name: str, description: str, risk: Optional[str] = None):
    """工具注册装饰器

    用法:
        @tool(name="get_market_overview", description="获取全市场ETF行情概览")
        def get_market_overview(db: Session, limit: int = 50) -> dict:
            ...

    注意: 函数的第一个参数必须是 db: Session（自动注入，不暴露给LLM）

    Args:
        name: 工具名称
        description: 工具描述
        risk: 风险级别 "read"/"write"；缺省按 _WRITE_TOOLS 表归类，write 需审批
    """

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        hints = get_type_hints(func, include_extras=True) if hasattr(func, "__annotations__") else {}
        doc_param_desc = _parse_docstring_params(func)

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            # 跳过 db 参数（自动注入）
            if param_name == "db":
                continue

            annotation = hints.get(param_name, param.annotation)
            base_ann, ann_desc = _unwrap_annotated(annotation)
            prop = _python_type_to_json_schema(base_ann)

            # 参数描述：Annotated 元数据 > docstring > 无
            desc = ann_desc or doc_param_desc.get(param_name)
            if desc:
                prop["description"] = desc

            # 从 docstring 或默认值推断描述
            if param.default is not inspect.Parameter.empty:
                # None 默认值不写进 schema（对非 null 类型是无意义提示）
                if param.default is not None:
                    prop["default"] = param.default
            else:
                required.append(param_name)

            properties[param_name] = prop

        parameters_schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters_schema["required"] = required

        effective_risk = risk or ("write" if name in _WRITE_TOOLS else "read")
        tool_def = ToolDef(
            name=name,
            description=description,
            func=func,
            parameters=parameters_schema,
            risk_level=effective_risk,
            requires_approval=effective_risk == "write",
        )
        _TOOL_REGISTRY[name] = tool_def
        logger.debug(f"注册工具: {name}")
        return func

    return decorator


class ArgumentError(ValueError):
    """LLM 传参无法按 schema 纠正"""


_BOOL_WORDS = {
    "true": True, "false": False, "1": True, "0": False,
    "yes": True, "no": False,
}


def _to_number(value: Any):
    if isinstance(value, bool):
        raise ArgumentError(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise ArgumentError(value)
    raise ArgumentError(value)


def _from_json_text(value: Any):
    """LLM 常把数组/对象序列化成 JSON 字符串传入，先尝试解析"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            raise ArgumentError(value)
    return value


def _coerce_value(name: str, value: Any, schema: Dict) -> Any:
    """按 schema 声明的类型纠正传参，纠不了就报错（错误信息回给 LLM 自我修正）"""
    expected = schema.get("type")

    if expected == "integer":
        number = _to_number(value)
        if float(number).is_integer():
            return int(number)
        raise ArgumentError(number)
    if expected == "number":
        return _to_number(value)
    if expected == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _BOOL_WORDS:
            return _BOOL_WORDS[value.strip().lower()]
        raise ArgumentError(value)
    if expected == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)   # 代码类参数（如 510300）按字符串收下
        raise ArgumentError(value)
    if expected == "array":
        parsed = _from_json_text(value)
        if isinstance(parsed, (list, tuple, set)):
            return list(parsed)
        raise ArgumentError(parsed)
    if expected == "object":
        parsed = _from_json_text(value)
        if isinstance(parsed, dict):
            return parsed
        raise ArgumentError(parsed)

    return value


def _validate_arguments(tool_def: "ToolDef", arguments: Dict) -> tuple:
    """执行前校验并纠正参数，返回 (可用 kwargs, 错误信息或 None)"""
    schema = tool_def.parameters or {}
    props = schema.get("properties") or {}

    unknown = [k for k in arguments if k not in props]
    if unknown:
        return None, (f"参数 {unknown} 不属于工具 {tool_def.name}，"
                      f"可用参数: {sorted(props)}")
    missing = [n for n in schema.get("required", []) if n not in arguments]
    if missing:
        return None, f"缺少必填参数: {missing}"

    coerced: Dict[str, Any] = {}
    for name, value in arguments.items():
        try:
            coerced[name] = _coerce_value(name, value, props.get(name) or {})
        except ArgumentError:
            expected = (props.get(name) or {}).get("type", "未知")
            return None, f"参数 {name} 类型不符：期望 {expected}，收到 {value!r}"
    return coerced, None


class ToolRegistry:
    """工具注册中心 - 生成 OpenAI function calling schema 并执行工具"""

    def get_openai_tools(self) -> List[Dict]:
        """返回所有工具的 OpenAI tools 格式定义"""
        return [t.to_openai_schema() for t in _TOOL_REGISTRY.values()]

    def get_tool_names(self) -> List[str]:
        """返回所有已注册工具名称"""
        return list(_TOOL_REGISTRY.keys())

    def execute(self, tool_name: str, arguments: Dict, db: Session) -> Dict:
        """执行指定工具，返回结果

        Args:
            tool_name: 工具名称
            arguments: LLM 传入的参数（不含 db）
            db: 数据库会话（自动注入）

        Returns:
            工具执行结果字典；参数校验不通过时返回 error 字段（不抛穿给 LLM）
        """
        tool_def = _TOOL_REGISTRY.get(tool_name)
        if not tool_def:
            return {"error": f"未知工具: {tool_name}"}

        kwargs, arg_error = _validate_arguments(tool_def, arguments or {})
        if arg_error:
            logger.warning(f"工具 {tool_name} 参数校验未通过: {arg_error}")
            return {"error": arg_error}

        try:
            # 注入 db 参数
            kwargs = {"db": db, **kwargs}
            result = tool_def.func(**kwargs)
            return result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            logger.error(f"工具 {tool_name} 执行失败: {e}", exc_info=True)
            return {"error": f"工具执行失败: {str(e)}"}

    def get_tool(self, name: str) -> Optional[ToolDef]:
        """获取工具定义"""
        return _TOOL_REGISTRY.get(name)


# 全局单例
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        # 导入所有工具模块以触发注册
        from app.tools import market_tools  # noqa: F401
        from app.tools import strategy_tools  # noqa: F401
        from app.tools import portfolio_tools  # noqa: F401
        from app.tools import risk_tools  # noqa: F401
        from app.tools import analysis_tools  # noqa: F401
        from app.tools import ops_tools  # noqa: F401

        _registry = ToolRegistry()

        # 注册 load_skill 工具（skill 文档动态加载）
        _register_load_skill()

        # 注册 MCP 桥接工具（MCP SDK 未安装或 server 未配置时自动跳过）
        try:
            from app.agent_core.mcp_bridge import get_mcp_bridge
            bridge = get_mcp_bridge()
            if bridge is not None:
                bridge.register_all(_TOOL_REGISTRY)
        except Exception as e:
            logger.warning(f"MCP 工具注册跳过: {e}")

        logger.info(f"Tool Registry 初始化完成，共 {len(_TOOL_REGISTRY)} 个工具")
    return _registry


def _register_load_skill():
    """注册 load_skill 工具 - 加载 skill 文档全文供 LLM 使用"""
    from app.agent_core.skill_manager import get_skill_manager

    def load_skill(db: Optional[Session] = None, name: str = "") -> dict:
        """加载指定技能文档全文"""
        sm = get_skill_manager()
        body = sm.load_skill(name)
        if body is None:
            available = ", ".join(sm.get_skill_names()) or "无"
            return {"error": f"未知技能: {name}，可用技能: {available}"}
        return {"skill": name, "content": body}

    _TOOL_REGISTRY["load_skill"] = ToolDef(
        name="load_skill",
        description="加载指定技能（skill）文档全文，获取调用外部工具（如 MCP 工具）的使用指引。技能名称见系统提示「可用技能」列表。",
        func=load_skill,
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "技能名称"}},
            "required": ["name"],
        },
    )
