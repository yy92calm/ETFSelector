"""
Tool Registry - LLM 工具注册中心

提供 @tool 装饰器注册工具，自动生成 OpenAI Function Calling schema，
并支持按名称执行工具。
"""

import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, get_type_hints
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 全局工具注册表
_TOOL_REGISTRY: Dict[str, "ToolDef"] = {}


class ToolDef:
    """工具定义"""

    def __init__(self, name: str, description: str, func: Callable, parameters: Dict):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters  # JSON Schema

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


def _python_type_to_json_schema(annotation) -> Dict:
    """将 Python 类型注解转为 JSON Schema 类型"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is str:
        return {"type": "string"}
    if annotation is dict or (hasattr(annotation, "__origin__") and annotation.__origin__ is dict):
        return {"type": "object"}
    if annotation is list or (hasattr(annotation, "__origin__") and annotation.__origin__ is list):
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def tool(name: str, description: str):
    """工具注册装饰器

    用法:
        @tool(name="get_market_overview", description="获取全市场ETF行情概览")
        def get_market_overview(db: Session, limit: int = 50) -> dict:
            ...

    注意: 函数的第一个参数必须是 db: Session（自动注入，不暴露给LLM）
    """

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            # 跳过 db 参数（自动注入）
            if param_name == "db":
                continue

            annotation = hints.get(param_name, param.annotation)
            prop = _python_type_to_json_schema(annotation)

            # 从 docstring 或默认值推断描述
            if param.default is not inspect.Parameter.empty:
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

        tool_def = ToolDef(
            name=name,
            description=description,
            func=func,
            parameters=parameters_schema,
        )
        _TOOL_REGISTRY[name] = tool_def
        logger.debug(f"注册工具: {name}")
        return func

    return decorator


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
            工具执行结果字典
        """
        tool_def = _TOOL_REGISTRY.get(tool_name)
        if not tool_def:
            return {"error": f"未知工具: {tool_name}"}

        try:
            # 注入 db 参数
            kwargs = {"db": db, **arguments}
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

        _registry = ToolRegistry()

        # 注册 load_skill 工具（skill 文档动态加载）
        _register_load_skill()

        # 注册 MCP 桥接工具（未配置 MCP server 时自动跳过）
        from app.agent_core.mcp_bridge import get_mcp_bridge
        get_mcp_bridge().register_all(_TOOL_REGISTRY)

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
